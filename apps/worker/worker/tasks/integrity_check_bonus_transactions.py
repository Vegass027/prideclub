from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.models.penalty import Penalty
from app.models.transaction import Transaction


async def _process(*, session_factory=None) -> dict:
    log = get_logger("worker.integrity_bonus")
    """Алерт если penalty.bonus_applied=true без связанной транзакции."""
    from db.session import async_session_factory  # type: ignore[import-not-found]

    factory = session_factory if session_factory is not None else async_session_factory

    orphans = 0
    async with factory() as session:
        penalties = (
            await session.execute(
                select(Penalty).where(Penalty.bonus_applied.is_(True))
            )
        ).scalars().all()

        for p in penalties:
            tx_count = (
                await session.execute(
                    select(Transaction).where(Transaction.related_penalty_id == p.id)
                )
            ).all()
            if not tx_count:
                orphans += 1

    if orphans > 0:
        log.warning("integrity_bonus_orphans", extra={"count": orphans})
    return {"orphans": orphans}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.integrity_check_bonus_transactions.run")
    def run() -> dict:
        import asyncio

        return asyncio.run(_process())
else:
    run = _process