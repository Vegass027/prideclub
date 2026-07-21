"""Тесты для worker-задачи `integrity_check_bonus_transactions`.

По docs/06-data-model §6 (финансовая целостность): любой `penalties.bonus_applied=true`
должен иметь связанную `transactions` строку с `type='bonus_catch'` (или
'bonus_subscription' / 'bonus_points'). Если нет — это orphan, нужен алерт.
"""
from __future__ import annotations

from datetime import date

import pytest


@pytest.mark.asyncio
async def test_integrity_no_orphans_returns_zero(worker_db):
    """Когда все bonus_applied=true имеют связанные транзакции → orphans=0."""
    from worker.tasks.integrity_check_bonus_transactions import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=300)
        habit = await worker_db.add_habit(session, id="00000000-0000-0000-0000-0000000000aa")
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        p = await worker_db.add_penalty(
            session,
            violator_membership_id=m.id,
            catcher_membership_id=m.id,
            amount=100,
            reason="caught",
            on_date=date(2026, 7, 21),
            bonus_applied=True,
        )
        await worker_db.add_transaction(
            session,
            user_id=user.id,
            type="bonus_catch",
            amount=1,
            related_penalty_id=p.id,
            related_membership_id=m.id,
        )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)
    assert result == {"orphans": 0}


@pytest.mark.asyncio
async def test_integrity_detects_orphan_penalty(worker_db):
    """penalty с bonus_applied=true без связанной transaction → orphans=1."""
    from worker.tasks.integrity_check_bonus_transactions import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=301)
        habit = await worker_db.add_habit(session, id="00000000-0000-0000-0000-0000000000bb")
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await worker_db.add_penalty(
            session,
            violator_membership_id=m.id,
            catcher_membership_id=m.id,
            amount=100,
            reason="caught",
            on_date=date(2026, 7, 21),
            bonus_applied=True,
        )
        # намеренно без transaction
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)
    assert result == {"orphans": 1}


@pytest.mark.asyncio
async def test_integrity_ignores_penalties_without_bonus_applied(worker_db):
    """penalty с bonus_applied=false → не orphan (бонус ещё не начислен)."""
    from worker.tasks.integrity_check_bonus_transactions import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=302)
        habit = await worker_db.add_habit(session, id="00000000-0000-0000-0000-0000000000cc")
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await worker_db.add_penalty(
            session,
            violator_membership_id=m.id,
            catcher_membership_id=None,
            amount=100,
            reason="window_closed_no_catch",
            on_date=date(2026, 7, 21),
            bonus_applied=False,
        )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)
    assert result == {"orphans": 0}


@pytest.mark.asyncio
async def test_integrity_counts_multiple_orphans(worker_db):
    """Несколько orphan'ов → корректный счёт."""
    from worker.tasks.integrity_check_bonus_transactions import _process

    async with worker_db.session_factory() as session:
        for i in range(3):
            user = await worker_db.add_user(session, id=310 + i)
            habit = await worker_db.add_habit(
                session,
                id=f"00000000-0000-0000-0000-00000000000{i}",
                chat_id=-1000 - i,
            )
            m = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id
            )
            await worker_db.add_penalty(
                session,
                violator_membership_id=m.id,
                catcher_membership_id=m.id,
                amount=100,
                reason="caught",
                on_date=date(2026, 7, 21),
                bonus_applied=True,
            )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)
    assert result == {"orphans": 3}
