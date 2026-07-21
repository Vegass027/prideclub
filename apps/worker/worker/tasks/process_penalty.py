from __future__ import annotations

import os
from datetime import date
from typing import Protocol

from app.core.logging import get_logger
from app.core.exceptions import PenaltyAlreadyProcessedError
from app.core.constants import MembershipStatus
from app.models.membership import Membership
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.penalty_service import PenaltyService
from db.session import async_session_factory  # type: ignore[import-not-found]


async def _pause_violator(payload: dict, *, factory) -> None:
    """При deposit_exhausted переводим violator в PAUSED в отдельной транзакции.

    `apply_catch` мутирует `violator.status = PAUSED`, но raise+rollback
    откатывают это изменение. Поэтому здесь — короткая отдельная транзакция,
    которая гарантированно сохраняет PAUSED в БД.
    """
    from sqlalchemy import select

    violator_id = payload["violator_membership_id"]
    async with factory() as session:
        result = await session.execute(
            select(Membership).where(Membership.id == violator_id)
        )
        m = result.scalar_one_or_none()
        if m is not None and m.status == MembershipStatus.ACTIVE:
            m.status = MembershipStatus.PAUSED
            await session.commit()


class RedisPort(Protocol):
    """Тот же протокол, что в PenaltyService — единый контракт."""

    async def incr_catch(self, catcher_user_id: int) -> int: ...


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
    снаружи. Без redis_port rate-limit отключён (полезно для тестов). Без
    session_factory используется дефолтная из `db.session`.
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
                redis_port=redis_port,
            )
            penalty = await service.apply_catch(
                catcher_user_id=payload["catcher_user_id"],
                violator_membership_id=payload["violator_membership_id"],
                club_date=date.fromisoformat(payload["club_date"]),
                catcher_membership_id=payload.get("catcher_membership_id"),
            )
            await session.commit()
            log.info(
                "worker_penalty_ok",
                extra={
                    "penalty_id": str(penalty.id),
                    "violator_membership_id": payload["violator_membership_id"],
                    "amount": penalty.amount,
                },
            )
            return {"ok": True, "penalty_id": str(penalty.id)}
        except PenaltyAlreadyProcessedError as exc:
            await session.rollback()
            log.info("worker_penalty_duplicate", extra={"code": exc.code})
            # Особый случай: "deposit_exhausted" требует, чтобы violator перешёл
            # в PAUSED в БД. Service ставит violator.status = PAUSED, но rollback
            # откатывает это изменение. Поэтому обрабатываем отдельным коммитом.
            if exc.code == "deposit_exhausted":
                await _pause_violator(payload, factory=factory)
            return {"ok": True, "duplicate": True, "code": exc.code}
        except IntegrityError as exc:
            await session.rollback()
            log.info("worker_penalty_integrity", extra={"err": str(exc)})
            return {"ok": True, "duplicate": True}
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.error("worker_penalty_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore


def _build_production_redis_port() -> RedisPort | None:
    """Создаёт production-Redis-клиент для rate-limit."""
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
        autoretry_for=(Exception,),
        dont_autoretry_for=(PenaltyAlreadyProcessedError,),
        retry_backoff=True,
        retry_backoff_max=60,
        retry_jitter=True,
    )
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        import asyncio

        redis_port = _build_production_redis_port()
        return asyncio.run(
            _process(payload, redis_port=redis_port, session_factory=async_session_factory)
        )
else:
    run = _process