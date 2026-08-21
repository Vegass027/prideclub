from __future__ import annotations

import os
import traceback
from datetime import date
from typing import Protocol

from app.core.logging import get_logger
from app.core.exceptions import PenaltyAlreadyProcessedError
from app.models.membership import Membership
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.services.penalty_service import PenaltyService
from db.session import async_session_factory  # type: ignore[import-not-found]
from worker.services.character_factory import _build_character_service


async def _pause_violator(payload: dict, *, factory) -> None:
    """При deposit_exhausted пересчитываем паузу в отдельной транзакции.

    `apply_catch` бросает PenaltyAlreadyProcessedError("deposit_exhausted")
    ДО recompute_pause_status, поэтому ни депозит-списание, ни статус не
    сохраняются в БД. Здесь — короткая отдельная транзакция, которая
    через MembershipService.recompute_pause_status выставляет корректный
    статус для ВСЕХ клубов юзера (PAUSED если депозит < penalty, ACTIVE
    иначе). См. Pravki-deposit-sse.md §Z-2.5/§Z-2.6, правка B:
    "Единственный источник статуса membership при изменении депозита —
    recompute_pause_status".
    """
    from sqlalchemy import select

    from app.repositories.membership_repository import MembershipRepository
    from app.repositories.user_repository import UserRepository
    from app.services.membership_service import MembershipService

    violator_id = payload["violator_membership_id"]
    async with factory() as session:
        # Нужен user_id — берём из membership.
        result = await session.execute(
            select(Membership.user_id).where(Membership.id == violator_id)
        )
        row = result.first()
        if row is None:
            return
        user_id = row[0]

        # MembershipService.recompute_pause_status внутри берёт
        # user_repo.lock_for_update(user_id) — сериализуется с любым
        # параллельным catch/topup этого юзера.
        await MembershipService(
            session=session,
            membership_repo=MembershipRepository(session),
            user_repo=UserRepository(session),
        ).recompute_pause_status(user_id)
        await session.commit()


async def _send_catch_notification(
    *,
    payload: dict,
    bot_token: str,
    session_factory,
) -> None:
    """Best-effort публикация события поимки в топик уведомлений.

    Вызывается ПОСЛЕ успешного коммита основной транзакции. Не бросает
    наружу — уведомление не должно ломать worker-таску, штраф уже в БД.
    Открывает короткую сессию только для чтения habit/membership/user и
    публикации в Telegram через NotificationService.
    """
    from app.models.habit import Habit
    from app.models.user import User
    from app.services.notification_service import NotificationService

    violator_membership_id = payload["violator_membership_id"]
    catcher_membership_id = payload.get("catcher_membership_id")
    catcher_user_id = payload.get("catcher_user_id")
    amount = int(payload.get("amount") or 0)

    factory = (
        session_factory if session_factory is not None else async_session_factory
    )

    async with factory() as session:
        violator = await session.get(Membership, violator_membership_id)
        if violator is None:
            return
        habit = await session.get(Habit, str(violator.habit_id))
        if habit is None:
            return
        violator_user = await session.get(User, int(violator.user_id))
        catcher_membership = None
        catcher_user = None
        if catcher_membership_id:
            catcher_membership = await session.get(
                Membership, catcher_membership_id
            )
        if catcher_user_id:
            catcher_user = await session.get(User, int(catcher_user_id))

    service = NotificationService(
        bot_token=bot_token,
        user_lookup=None,
    )
    await service.notify_catch(
        habit=habit,
        catcher_membership=catcher_membership,
        catcher_user=catcher_user,
        violator_membership=violator,
        violator_user=violator_user,
        penalty_amount_kopecks=amount,
    )


class RedisPort(Protocol):
    """Тот же протокол, что в PenaltyService — единый контракт."""

    async def incr_catch(self, catcher_user_id: int) -> int: ...


class RateLimitDisabledError(RuntimeError):
    """Catch-rate-limit отключён потому что Redis недоступен.

    T5: используется прод-обёрткой `run()` — fail-closed семантика.
    Без rate-limit ловитель может делать catch-действия без лимита;
    в проде лучше отказать и пойти в Celery retry, чем пропустить штраф
    без защиты.
    """


async def _process(
    payload: dict,
    *,
    redis_port: RedisPort | None = None,
    session_factory=None,
) -> dict:
    """Чистая async-функция для тестов и для Celery-обёртки.

    Транзакция: одна на всю таску.
    Идемпотентность: уникальный индекс (membership_id, date, reason) в `penalties`
    + `PenaltyAlreadyProcessedError` → идемпотентный ok-ответ.

    DI: redis_port (опциональный) и session_factory (опциональный) передаются
    снаружи. Без redis_port rate-limit отключён (`PenaltyService` увидит
    `self._redis is None` и пропустит check). Это **fail-OPEN** — допустимо
    только в тестах и dev-режиме. Прод-обёртка `run()` проверяет `redis_port`
    и бросает `RateLimitDisabledError` если None (см. T5).
    """
    log = get_logger("worker.process_penalty")
    from sqlalchemy.exc import IntegrityError

    factory = session_factory if session_factory is not None else async_session_factory

    async with factory() as session:
        try:
service = PenaltyService(
                session=session,
                habit_repo=HabitRepository(session),
                membership_repo=MembershipRepository(session),
                checkin_repo=CheckinRepository(session),
                suspicious_repo=SuspiciousPairsRepository(),
                redis_port=redis_port,
                character_service=_build_character_service(session),
            )
            penalty = await service.apply_catch(
                catcher_user_id=payload["catcher_user_id"],
                violator_membership_id=payload["violator_membership_id"],
                club_date=date.fromisoformat(payload["club_date"]),
                catcher_membership_id=payload.get("catcher_membership_id"),
            )
            penalty_amount = int(penalty.amount)
            await session.commit()
            log.info(
                "worker_penalty_ok",
                extra={
                    "penalty_id": str(penalty.id),
                    "violator_membership_id": payload["violator_membership_id"],
                    "amount": penalty_amount,
                },
            )

            bot_token = os.getenv("BOT_TOKEN", "")
            if bot_token:
                notif_payload = {
                    **payload,
                    "amount": penalty_amount,
                }
                try:
                    await _send_catch_notification(
                        payload=notif_payload,
                        bot_token=bot_token,
                        session_factory=factory,
                    )
                except Exception as exc:  # noqa: BLE001
                    # Pravki-de-risk-2026-08-21: добавлен traceback для
                    # диагностики — раньше notification_failed мог тихо
                    # проглотить любую ошибку без возможности понять причину.
                    log.warning(
                        "worker_penalty_notification_failed",
                        extra={
                            "err": str(exc),
                            "err_type": type(exc).__name__,
                            "stack": traceback.format_exc(),
                        },
                    )

            return {"ok": True, "penalty_id": str(penalty.id)}
        except PenaltyAlreadyProcessedError as exc:
            await session.rollback()
            log.info("worker_penalty_duplicate", extra={"code": exc.code})
            # Особый случай: "deposit_exhausted" требует, чтобы violator перешёл
            # в PAUSED в БД. apply_catch raise'ит ДО recompute_pause_status, поэтому
            # изменение статуса откатывается. Здесь отдельной транзакцией вызываем
            # recompute_pause_status — он пересчитает статус для ВСЕХ клубов юзера.
            if exc.code == "deposit_exhausted":
                await _pause_violator(payload, factory=factory)
            return {"ok": True, "duplicate": True, "code": exc.code}
        except IntegrityError as exc:
            await session.rollback()
            log.info("worker_penalty_integrity", extra={"err": str(exc)})
            return {"ok": True, "duplicate": True}
        except Exception as exc:  # noqa: BLE001
            # Pravki-de-risk-2026-08-21: traceback обязателен для диагностики.
            # Без него worker_penalty_failed возвращал {"ok": False, "err": str(exc)}
            # и терял причину (например, TypeError при несовпадении сигнатуры
            # конструктора — реальный инцидент Z-2.5 с penalty_repo в
            # CheckinService.__init__). Теперь полный стек в логах.
            await session.rollback()
            log.error(
                "worker_penalty_failed",
                extra={
                    "err": str(exc),
                    "err_type": type(exc).__name__,
                    "stack": traceback.format_exc(),
                },
            )
            return {"ok": False, "err": str(exc)}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore


def _build_production_redis_port() -> RedisPort | None:
    """Создаёт production-Redis-клиент для rate-limit.

    Возвращает None если REDIS_URL не задан. Это легитимный кейс
    (env ещё не подгружен / dev-окружение) — но в проде прод-runner
    (`run()`) трактует None как `RateLimitDisabledError`.
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    import redis.asyncio as aioredis

    from app.services.catch_rate_limiter import RedisCatchRateLimiter

    return RedisCatchRateLimiter(aioredis.from_url(redis_url, decode_responses=True))


if celery_app is not None:

    @celery_app.task(
        name="worker.tasks.process_penalty.run",
        bind=True,
        max_retries=3,
        autoretry_for=(Exception, RateLimitDisabledError),
        dont_autoretry_for=(PenaltyAlreadyProcessedError,),
        retry_backoff=True,
        retry_backoff_max=60,
        retry_jitter=True,
    )
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        """Прод-обёртка (T5 — fail-CLOSED для catch-rate-limit).

        Если `_build_production_redis_port()` вернул None — это значит
        что Redis недоступен, и без rate-limit ловитель может спамить
        catch-действиями. Бросаем RateLimitDisabledError → Celery
        autoretry (до 3 раз с backoff).
        """
        import asyncio

        redis_port = _build_production_redis_port()
        if redis_port is None:
            log = get_logger("worker.process_penalty")
            log.error(
                "rate_limit_unavailable",
                extra={"reason": "redis_port_none"},
            )
            raise RateLimitDisabledError(
                "catch-rate-limit disabled: Redis not configured or unavailable"
            )
        return asyncio.run(
            _process(payload, redis_port=redis_port, session_factory=async_session_factory)
        )
else:
    run = _process