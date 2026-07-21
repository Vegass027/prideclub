from __future__ import annotations

from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.checkin_service import CheckinService
from app.services.catch_rate_limiter import RedisCatchRateLimiter
from app.services.proof_validator import ProofMessage
from app.services.today_cache import RedisTodayCache
from db.session import async_session_factory  # type: ignore[import-not-found]


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore


async def _process(payload: dict) -> dict:
    log = get_logger("worker.checkin")
    settings_payload = _load_settings()
    redis = aioredis.from_url(settings_payload["REDIS_URL"], decode_responses=True)
    limiter = RedisCatchRateLimiter(redis)

    async with async_session_factory() as session:  # type: ignore[name-defined]
        try:
            from app.core.constants import ProofType

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
                cache=RedisTodayCache(redis),
            )
            checkin = await service.process_checkin(
                user_id=payload["user_id"],
                habit_id=payload["habit_id"],
                proof=proof,
                proof_message_id=payload["message_id"],
                now_utc=datetime.now(tz=timezone.utc),
            )
            log.info("worker_checkin_ok", extra={"checkin_id": str(checkin.id)})
            return {"ok": True, "checkin_id": str(checkin.id)}
        except Exception as exc:  # noqa: BLE001
            log.error("worker_checkin_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}
        finally:
            await redis.aclose()


def _load_settings() -> dict:
    import os

    return {
        "DATABASE_URL": os.getenv("DATABASE_URL", ""),
        "REDIS_URL": os.getenv("REDIS_URL", "redis://redis:6379/0"),
    }


# Если Celery подключён — регистрируем таску.
if celery_app is not None:

    @celery_app.task(name="worker.tasks.process_checkin.run", bind=True, max_retries=3)
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        import asyncio

        return asyncio.run(_process(payload))
else:
    run = _process