from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.core.constants import ProofType
from app.core.exceptions import (
    CheckinAlreadyExistsError,
    CheckinWindowClosedError,
    CheckinWrongTopicError,
    MembershipNotActiveError,
    MembershipNotFoundError,
)
from app.core.logging import get_logger
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.checkin_service import CheckinService
from app.services.proof_validator import ProofMessage, ProofValidationError
from db.session import async_session_factory  # type: ignore[import-not-found]


class CachePort(Protocol):
    """Тот же протокол, что в CheckinService — единый контракт между слоями."""

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None: ...


async def _process(
    payload: dict,
    *,
    cache: CachePort | None = None,
    session_factory=None,
) -> dict:
    """Чистая async-функция для тестов и для Celery-обёртки.

    Идемпотентность обеспечивается UNIQUE-индексом
    `uq_checkins_membership_date (membership_id, date)` в БД.

    Транзакция: одна на всю таску. Service.flush() пишет строку, затем commit().
    При IntegrityError (дубль чек-ина) — rollback + идемпотентный ok-ответ.

    DI: cache (опциональный) и session_factory (опциональный) передаются
    снаружи. Это позволяет тестам не поднимать ни Redis, ни Postgres/SQLite —
    достаточно передать свою фабрику сессий. Прод-обёртка ниже создаёт
    настоящие Redis-клиент и фабрику по умолчанию.
    """
    log = get_logger("worker.checkin")
    from sqlalchemy.exc import IntegrityError

    factory = session_factory if session_factory is not None else async_session_factory

    async with factory() as session:
        try:
            proof = ProofMessage(
                proof_type=ProofType(payload["proof_type"]),
                text=payload.get("text"),
                video_note_duration=payload.get("duration_seconds"),
                photo_sizes=1 if payload["proof_type"] == "photo" else 0,
                message_date=datetime.fromisoformat(payload["message_sent_at"]),
            )
            service = CheckinService(
                session=session,
                habit_repo=HabitRepository(session),
                membership_repo=MembershipRepository(session),
                checkin_repo=CheckinRepository(session),
                cache=cache,
            )
            checkin, created = await service.process_checkin(
                user_id=payload["user_id"],
                habit_id=payload["habit_id"],
                proof=proof,
                proof_message_id=payload["message_id"],
                now_utc=datetime.now(tz=timezone.utc),
                message_thread_id=payload.get("message_thread_id"),
            )
            await session.commit()
            log.info(
                "worker_checkin_ok",
                extra={
                    "checkin_id": str(checkin.id),
                    "created": created,
                    "user_id": payload["user_id"],
                    "habit_id": payload["habit_id"],
                },
            )
            return {
                "ok": True,
                "checkin_id": str(checkin.id),
                "created": created,
                "duplicate": not created,
            }
        except CheckinAlreadyExistsError:
            await session.rollback()
            log.info("worker_checkin_duplicate", extra={"user_id": payload["user_id"]})
            return {"ok": True, "duplicate": True}
        except (
            ProofValidationError,
            CheckinWindowClosedError,
            CheckinWrongTopicError,
            MembershipNotActiveError,
            MembershipNotFoundError,
        ) as exc:
            await session.rollback()
            log.warning(
                "worker_checkin_rejected",
                extra={"code": getattr(exc, "code", "rejected"), "err": str(exc)},
            )
            return {"ok": False, "code": getattr(exc, "code", "rejected")}
        except IntegrityError as exc:
            await session.rollback()
            log.warning("worker_checkin_integrity", extra={"err": str(exc)})
            return {"ok": True, "duplicate": True}
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.error("worker_checkin_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore


def _build_production_cache() -> CachePort | None:
    """Создаёт production-Redis-клиент. Lazy import чтобы не тянуть redis
    при юнит-тестах."""
    import os

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    import redis.asyncio as aioredis

    from app.services.today_cache import RedisTodayCache

    return RedisTodayCache(aioredis.from_url(redis_url, decode_responses=True))


if celery_app is not None:

    @celery_app.task(
        name="worker.tasks.process_checkin.run",
        bind=True,
        max_retries=3,
        autoretry_for=(Exception,),
        dont_autoretry_for=(
            CheckinAlreadyExistsError,
            ProofValidationError,
            CheckinWindowClosedError,
            CheckinWrongTopicError,
            MembershipNotActiveError,
            MembershipNotFoundError,
        ),
        retry_backoff=True,
        retry_backoff_max=60,
        retry_jitter=True,
    )
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        import asyncio

        cache = _build_production_cache()
        return asyncio.run(_process(payload, cache=cache, session_factory=async_session_factory))
else:
    run = _process