from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.constants import PenaltyConfig
from app.core.logging import get_logger
from app.models.user import User


async def _process(*, session_factory=None) -> dict:
    log = get_logger("worker.expire_bonus_points")
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=PenaltyConfig.BONUS_POINTS_EXPIRY_DAYS)
    expired = 0
    from db.session import async_session_factory  # type: ignore[import-not-found]

    factory = session_factory if session_factory is not None else async_session_factory

    async with factory() as session:
        result = await session.execute(
            select(User).where(
                User.bonus_points > 0,
                User.bonus_points_updated_at.is_not(None),
                User.bonus_points_updated_at < cutoff,
            )
        )
        for user in result.scalars().all():
            user.bonus_points = 0
            user.bonus_points_updated_at = None
            expired += 1
        await session.commit()
    log.info("bonus_points_expired", extra={"count": expired})
    return {"expired": expired}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.expire_bonus_points.run")
    def run() -> dict:
        import asyncio

        return asyncio.run(_process())
else:
    run = _process