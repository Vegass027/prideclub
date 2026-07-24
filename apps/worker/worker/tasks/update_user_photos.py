"""Worker cron: обновить photo_file_id + локальный JPEG-кеш для активных юзеров.

Подход D (см. Pravki.md §7.1 v3, 2026-07-24):
    - Храним photo_file_id в БД (постоянный между вызовами Bot API).
    - Worker СКАЧИВАЕТ JPEG с Telegram CDN и сохраняет в
      <STATIC_DIR>/avatars/{user_id}.jpg (volume club_uploads).
    - Backend endpoint /api/v1/users/{id}/photo отдаёт FileResponse;
      nginx может отдавать через try_files (минуя FastAPI).
    - Токен бота НЕ утекает в клиент.

Нагрузка на Telegram Bot API:
    3 req на юзера (getUserProfilePhotos + getFile + GET CDN). На 1000
    users = 3000 req в сутки = 0.035 req/sec — в 860 раз ниже глобального
    лимита 30/sec. С sleep 50ms = 20 req/sec (большой запас).

Конфигурация (через worker.config.get_settings):
    - bot_token: токен основного бота.
    - STATIC_DIR: /app/static (env), default для аватарок = STATIC_DIR/avatars.
    - cron: раз в сутки в 04:00 UTC (celery_app.beat_schedule).
    - sleep между запросами: 50мс.

PII в логах:
    Логируем ТОЛЬКО user_id (числовой) и счётчики. Никаких first_name,
    photo_url, file_id в логах (AGENTS.md).
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

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

# Max JPEG size: 5 МБ. Защита от аномальных ответов Telegram.
_MAX_JPEG_BYTES = 5 * 1024 * 1024

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


async def _fetch_file_path(
    http: aiohttp.ClientSession,
    bot_token: str,
    file_id: str,
) -> str | None:
    """bot.getFile → {file_path} → возвращает file_path."""
    url = f"https://api.telegram.org/bot{bot_token}/getFile"
    try:
        async with http.get(
            url,
            params={"file_id": file_id},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning(
            "user_photos.get_file_network_error",
            extra={"file_id_prefix": file_id[:16], "err": str(exc)},
        )
        return None
    if not data.get("ok"):
        log.warning(
            "user_photos.get_file_telegram_error",
            extra={"file_id_prefix": file_id[:16]},
        )
        return None
    return (data.get("result") or {}).get("file_path")


async def _download_avatar_jpeg(
    http: aiohttp.ClientSession,
    bot_token: str,
    file_id: str,
) -> bytes | None:
    """Скачивает JPEG с Telegram CDN. None при ошибке."""
    file_path = await _fetch_file_path(http, bot_token, file_id)
    if not file_path:
        return None
    cdn_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    try:
        async with http.get(
            cdn_url,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                log.warning(
                    "user_photos.download_non_200",
                    extra={"file_id_prefix": file_id[:16], "status": resp.status},
                )
                return None
            content = await resp.content.read(_MAX_JPEG_BYTES + 1)
            if len(content) > _MAX_JPEG_BYTES:
                log.warning(
                    "user_photos.download_too_large",
                    extra={"file_id_prefix": file_id[:16], "size": len(content)},
                )
                return None
            return content
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning(
            "user_photos.download_network_error",
            extra={"file_id_prefix": file_id[:16], "err": str(exc)},
        )
        return None


def _avatar_path(avatars_dir: Path, user_id: int) -> Path:
    """Путь к локальному JPEG. user_id — int (Telegram user_id),
    path-traversal невозможен (нет пользовательского input)."""
    return avatars_dir / f"{user_id}.jpg"


async def _write_atomic(path: Path, content: bytes) -> None:
    """Записывает JPEG атомарно (write в .tmp, fsync, rename)."""
    def _sync_write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            f.write(content)
            f.flush()
            import os
            os.fsync(f.fileno())
        tmp_path.replace(path)

    await asyncio.to_thread(_sync_write)


async def _process(*, session_factory=None) -> dict:
    from db.session import async_session_factory  # type: ignore[import-not-found]

    factory = session_factory if session_factory is not None else async_session_factory
    settings = get_settings()
    if not settings.bot_token:
        log.error("user_photos.no_bot_token")
        return {"updated": 0, "skipped_no_avatar": 0, "errors": 0, "reason": "no_bot_token"}

    # Директория для локального кеша аватарок.
    static_dir = Path(os.environ.get("STATIC_DIR", "/app/static"))
    avatars_dir = static_dir / "avatars"
    await asyncio.to_thread(avatars_dir.mkdir, parents=True, exist_ok=True)

    updated = 0
    cached = 0
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

            local_path = _avatar_path(avatars_dir, user_id)
            file_was_cached = local_path.exists() and local_path.stat().st_size > 0

            # Если file_id не изменился И файл уже есть — пропускаем скачивание.
            async with factory() as session:
                user = await session.get(User, user_id)
                if user is None:
                    continue

                if file_id is None:
                    user.photo_file_id = None
                    user.photo_fetched_at = datetime.now(tz=UTC)
                    skipped_no_avatar += 1
                elif file_was_cached and user.photo_file_id == file_id:
                    # file_id не изменился + файл на диске → cache hit.
                    user.photo_fetched_at = datetime.now(tz=UTC)
                    cached += 1
                else:
                    # file_id новый или файл отсутствует → скачиваем.
                    jpeg_bytes = await _download_avatar_jpeg(
                        http, settings.bot_token, file_id
                    )
                    await asyncio.sleep(_REQUEST_SLEEP_SECONDS)
                    if jpeg_bytes is not None:
                        await _write_atomic(local_path, jpeg_bytes)
                        user.photo_file_id = file_id
                        user.photo_fetched_at = datetime.now(tz=UTC)
                        updated += 1
                    else:
                        # Скачивание не удалось, не обновляем file_id
                        # (мог быть временный сбой TG).
                        errors += 1
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
            "cached": cached,
            "skipped_no_avatar": skipped_no_avatar,
            "errors": errors,
        },
    )
    return {
        "updated": updated,
        "cached": cached,
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
