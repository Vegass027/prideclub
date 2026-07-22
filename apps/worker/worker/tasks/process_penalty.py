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
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
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
                suspicious_repo=SuspiciousPairsRepository(session),
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