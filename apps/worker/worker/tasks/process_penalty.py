from __future__ import annotations

from datetime import date, datetime, timezone

from app.core.exceptions import PenaltyAlreadyProcessedError
from app.core.logging import get_logger
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.checkin_repository import CheckinRepository
from app.services.catch_rate_limiter import RedisCatchRateLimiter
from app.services.penalty_service import PenaltyService


async def _process(payload: dict) -> dict:
    log = get_logger("worker.process_penalty")
    import os
    import redis.asyncio as aioredis

    redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    limiter = RedisCatchRateLimiter(redis)

    from db.session import async_session_factory  # type: ignore[import-not-found]

    async with async_session_factory() as session:  # type: ignore[name-defined]
        try:
            service = PenaltyService(
                session=session,
                habit_repo=HabitRepository(session),
                membership_repo=MembershipRepository(session),
                checkin_repo=CheckinRepository(session),
                redis_port=limiter,
            )
            penalty = await service.apply_catch(
                catcher_user_id=payload["catcher_user_id"],
                violator_membership_id=payload["violator_membership_id"],
                club_date=date.fromisoformat(payload["club_date"]),
                catcher_membership_id=payload.get("catcher_membership_id"),
            )
            log.info("penalty_applied", extra={"penalty_id": str(penalty.id)})
            return {"ok": True, "penalty_id": str(penalty.id)}
        except PenaltyAlreadyProcessedError as exc:
            return {"ok": False, "code": exc.code}
        except Exception as exc:  # noqa: BLE001
            log.error("penalty_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}
        finally:
            await redis.aclose()


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.process_penalty.run", bind=True, max_retries=3)
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        import asyncio

        return asyncio.run(_process(payload))
else:
    run = _process