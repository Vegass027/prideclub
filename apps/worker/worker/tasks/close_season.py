from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.repositories.habit_repository import HabitRepository
from app.services.season_service import SeasonService


async def _close_expired() -> dict:
    log = get_logger("worker.close_season")
    from db.session import async_session_factory  # type: ignore[import-not-found]

    closed = 0
    async with async_session_factory() as session:  # type: ignore[name-defined]
        habit_repo = HabitRepository(session)
        habits = await habit_repo.list_active()
        today = datetime.now(tz=timezone.utc).date()
        season_service = SeasonService(session)

        from sqlalchemy import select

        from app.models.season import Season

        for habit in habits:
            seasons = (
                await session.execute(
                    select(Season).where(
                        Season.habit_id == str(habit.id),
                        Season.status == "active",
                        Season.ends_at <= today,
                    )
                )
            ).scalars().all()

            for s in seasons:
                await season_service.close_season(season_id=str(s.id))
                closed += 1
        await session.commit()
    log.info("close_season_done", extra={"closed": closed})
    return {"closed": closed}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.close_season.run")
    def run() -> dict:
        import asyncio

        return asyncio.run(_close_expired())
else:
    run = _close_expired