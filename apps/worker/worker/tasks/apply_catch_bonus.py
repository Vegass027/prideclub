from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.repositories.bonus_rule_repository import BonusRuleRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.penalty_repository import PenaltyRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.repositories.user_repository import UserRepository
from app.services.bonus_service import BonusService


async def _process(payload: dict, *, session_factory=None) -> dict:
    log = get_logger("worker.bonus")
    from db.session import async_session_factory  # type: ignore[import-not-found]

    factory = session_factory if session_factory is not None else async_session_factory

    async with factory() as session:
        try:
            service = BonusService(
                session=session,
                membership_repo=MembershipRepository(session),
                penalty_repo=PenaltyRepository(session),
                user_repo=UserRepository(session),
                bonus_rule_repo=BonusRuleRepository(session),
                suspicious_repo=SuspiciousPairsRepository(session),
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

        return asyncio.run(_process(payload, session_factory=async_session_factory))
else:
    run = _process