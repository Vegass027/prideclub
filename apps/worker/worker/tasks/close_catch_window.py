from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from app.core.constants import MembershipStatus
from app.core.logging import get_logger
from app.models.habit import Habit
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.penalty_service import PenaltyService


async def _close_for_habit(session, habit: Habit, club_date: date) -> dict:
    membership_repo = MembershipRepository(session)
    habit_repo = HabitRepository(session)
    checkin_repo = __import__(
        "app.repositories.checkin_repository", fromlist=["CheckinRepository"]
    ).CheckinRepository(session)
    penalty_service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
    )

    members = await membership_repo.list_for_habit(str(habit.id))
    penalized = 0
    for membership in members:
        if membership.status != MembershipStatus.ACTIVE:
            continue
        existing = await checkin_repo.get_for_date(str(membership.id), club_date)
        if existing is not None:
            continue
        penalty = await penalty_service.apply_window_expired(
            violator_membership_id=str(membership.id),
            club_date=club_date,
        )
        if penalty is not None:
            penalized += 1
    return {"habit_id": str(habit.id), "penalized": penalized}


async def run_for_active_habits() -> dict:
    log = get_logger("worker.close_catch_window")
    from db.session import async_session_factory  # type: ignore[import-not-found]

    summary: list[dict] = []
    async with async_session_factory() as session:  # type: ignore[name-defined]
        habit_repo = HabitRepository(session)
        habits = await habit_repo.list_active()
        now_utc = datetime.now(tz=timezone.utc)
        for habit in habits:
            club_date = habit.club_date(now_utc)
            result = await _close_for_habit(session, habit, club_date)
            summary.append(result)
        await session.commit()
    log.info("close_catch_window_done", extra={"summary": summary})
    return {"summary": summary}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.close_catch_window.run_for_active_habits")
    def run_for_active_habits_celery() -> dict:  # type: ignore[no-redef]
        import asyncio

        return asyncio.run(run_for_active_habits())

    run_for_active_habits = run_for_active_habits_celery