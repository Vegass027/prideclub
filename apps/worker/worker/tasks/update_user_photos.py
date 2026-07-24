"""Worker cron: обновить photo_file_id для активных пользователей клубов.

Подход C' (см. Pravki.md §7.1):
    - Храним только photo_file_id (постоянный между вызовами Bot API).
    - photo_url в лидерборде — относительный путь
      /api/v1/users/{user_id}/photo → backend делает 307 redirect на Telegram CDN.
    - Токен бота остаётся server-side.

Нагрузка на Telegram Bot API:
    2 req на юзера (getUserProfilePhotos + НЕ getFile — file_id уже
    постоянный). На 1000 users = 2000 req в сутки = 0.023 req/sec —
    в 1300 раз ниже глобального лимита 30/sec.

Конфигурация (через worker.config.get_settings):
    - bot_token: токен основного бота (тот же что в backend).
    - cron: раз в сутки в 04:00 UTC (в celery_app.beat_schedule).
    - sleep между запросами: 50мс (= 20 req/sec) — большой запас.
      Убрать sleep при hard-launch когда проверим что всё стабильно.

PII в логах:
    Логируем ТОЛЬКО user_id (числовой) и счётчики. Никаких first_name,
    photo_url, file_id в логах (AGENTS.md).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aiohttp
from sqlalchemy import distinct, select

from app.core.constants import MembershipStatus
from app.models.membership import Membership
from app.models.user import User
from app.core.logging import get_logger
from worker.config import get_settings


# Throttle между запросами к Bot API. 50ms = 20 req/sec — большой запас
# ниже глобального лимита 30/sec. Убрать после hard-launch.
_REQUEST_SLEEP_SECONDS = 0.05

log = get_logger("worker.update_user_photos")


async def _fetch_photo_file_id(
    http: aiohttp.ClientSession,
    bot_token: str,
    user_id: int,
) -> str | None:
    """Возвращает file_id smallest-фото или None если аватарки нет / ошибка."""
    url = f"https://api.telegram.org/bot{bot_token}/getUserProfilePhotos"
    try:
        async with http.get(
            url,
            params={"user_id": user_id, "limit": 1},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning(
            "user_photos.network_error",
            extra={"user_id": user_id, "err": str(exc)},
        )
        return None

    if not data.get("ok"):
        description = data.get("description", "telegram_error")
        log.warning(
            "user_photos.telegram_error",
            extra={"user_id": user_id, "description": description},
        )
        return None

    result = data.get("result") or {}
    total_count = result.get("total_count", 0)
    if total_count == 0:
        return None

    photos = result.get("photos") or []
    if not photos or not photos[0]:
        return None

    # photos[0] — массив PhotoSize от smallest до biggest.
    # Берём последний (= самый большой из доступных, обычно 90x90).
    last = photos[0][-1]
    return last.get("file_id")


async def _process(*, session_factory=None) -> dict:
    from db.session import async_session_factory  # type: ignore[import-not-found]

    factory = session_factory if session_factory is not None else async_session_factory
    settings = get_settings()
    if not settings.bot_token:
        log.error("user_photos.no_bot_token")
        return {"updated": 0, "skipped_no_avatar": 0, "errors": 0, "reason": "no_bot_token"}

    updated = 0
    skipped_no_avatar = 0
    errors = 0

    async with factory() as session:
        # Активные пользователи — distinct user_id из ACTIVE memberships.
        user_ids = (
            await session.execute(
                select(distinct(Membership.user_id)).where(
                    Membership.status == MembershipStatus.ACTIVE
                )
            )
        ).scalars().all()

    if not user_ids:
        log.info("user_photos.no_active_users")
        return {"updated": 0, "skipped_no_avatar": 0, "errors": 0}

    log.info("user_photos.start", extra={"user_count": len(user_ids)})

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10),
    ) as http:
        for user_id in user_ids:
            file_id = await _fetch_photo_file_id(http, settings.bot_token, user_id)
            await asyncio.sleep(_REQUEST_SLEEP_SECONDS)

            async with factory() as session:
                user = await session.get(User, user_id)
                if user is None:
                    continue
                if file_id is None:
                    user.photo_file_id = None
                    user.photo_fetched_at = datetime.now(tz=UTC)
                    skipped_no_avatar += 1
                else:
                    user.photo_file_id = file_id
                    user.photo_fetched_at = datetime.now(tz=UTC)
                    updated += 1
                try:
                    await session.commit()
                except Exception as exc:  # noqa: BLE001
                    errors += 1
                    log.warning(
                        "user_photos.db_error",
                        extra={"user_id": user_id, "err": str(exc)},
                    )

    log.info(
        "user_photos.done",
        extra={
            "updated": updated,
            "skipped_no_avatar": skipped_no_avatar,
            "errors": errors,
        },
    )
    return {
        "updated": updated,
        "skipped_no_avatar": skipped_no_avatar,
        "errors": errors,
    }


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.update_user_photos.run")
    def run() -> dict:
        return asyncio.run(_process())
else:
    run = _process
