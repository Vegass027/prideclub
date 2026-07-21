from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.repositories.membership_repository import MembershipRepository
from app.services.bonus_service import BonusService
from app.repositories.habit_repository import HabitRepository


async def _process(payload: dict) -> dict:
    log = get_logger("worker.bonus")
    from db.session import async_session_factory  # type: ignore[import-not-found]

    async with async_session_factory() as session:  # type: ignore[name-defined]
        try:
            service = BonusService(
                session=session,
                membership_repo=MembershipRepository(session),
            )
            applied = await service.apply_catch_bonus(
                catcher_membership_id=payload["catcher_membership_id"],
                penalty_id=payload["penalty_id"],
            )
            await session.commit()
            return {"ok": True, "applied": applied}
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.error("bonus_apply_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.apply_catch_bonus.run")
    def run(payload: dict) -> dict:
        import asyncio

        return asyncio.run(_process(payload))
else:
    run = _process