"""Тесты для worker-задачи `close_catch_window`.

По docs/06-data-model §4.4: в конце окна чек-ина + 1 час cron обходит все
активные привычки, для каждого активного member без чек-ина за club_date
создаёт штраф с `reason='window_closed_no_catch'` через идемпотентный
INSERT ON CONFLICT DO NOTHING.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest


@pytest.mark.asyncio
async def test_close_window_creates_penalty_for_missing_checkin(worker_db):
    """Member без чек-ина → штраф с reason='window_closed_no_catch'."""
    from app.core.constants import PenaltyReason
    from sqlalchemy import select

    from app.models.penalty import Penalty
    from app.models.transaction import Transaction
    from worker.tasks.close_catch_window import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=100, first_name="Alex")
        habit = await worker_db.add_habit(
            session,
            id="00000000-0000-0000-0000-000000000001",
            chat_id=-1001,
            penalty_amount=200,
        )
        membership = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id, deposit_balance=1000
        )
        await session.commit()

    club_date = date(2026, 7, 21)
    _ = datetime(
        club_date.year, club_date.month, club_date.day, 12, 0, tzinfo=ZoneInfo("UTC")
    )

    result = await _process()

    assert result["summary"] == [
        {"habit_id": habit.id, "penalized": 1}
    ]

    async with worker_db.session_factory() as session:
        penalties = (await session.execute(select(Penalty))).scalars().all()
        assert len(penalties) == 1
        p = penalties[0]
        assert p.membership_id == membership.id
        assert p.catcher_membership_id is None
        assert p.reason == PenaltyReason.WINDOW_CLOSED_NO_CATCH.value
        assert p.amount == 200
        assert p.date == club_date

        txs = (await session.execute(select(Transaction))).scalars().all()
        assert len(txs) == 1
        assert txs[0].amount == -200
        assert txs[0].related_penalty_id == p.id


@pytest.mark.asyncio
async def test_close_window_skips_member_with_checkin(worker_db):
    """Если чек-ин уже есть за club_date → штраф не создаётся."""
    from sqlalchemy import select

    from app.models.penalty import Penalty
    from worker.tasks.close_catch_window import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=101)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000002"
        )
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id, deposit_balance=500
        )
        await worker_db.add_checkin(
            session, membership_id=m.id, on_date=date(2026, 7, 21)
        )
        await session.commit()

    result = await _process()
    assert result["summary"] == [{"habit_id": habit.id, "penalized": 0}]

    async with worker_db.session_factory() as session:
        penalties = (await session.execute(select(Penalty))).scalars().all()
        assert penalties == []


@pytest.mark.asyncio
async def test_close_window_idempotent_second_run(worker_db):
    """Повторный запуск cron'а в тот же день → не дублирует штрафы."""
    from sqlalchemy import select, func

    from app.models.penalty import Penalty
    from worker.tasks.close_catch_window import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=102)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000003"
        )
        await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id, deposit_balance=500
        )
        await session.commit()

    await _process()
    await _process()

    async with worker_db.session_factory() as session:
        count = (
            await session.execute(select(func.count(Penalty.id)))
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_close_window_skips_inactive_membership(worker_db):
    """Membership в paused/left → пропускается."""
    from sqlalchemy import select

    from app.core.constants import MembershipStatus
    from app.models.penalty import Penalty
    from worker.tasks.close_catch_window import _process

    async with worker_db.session_factory() as session:
        u1 = await worker_db.add_user(session, id=103)
        u2 = await worker_db.add_user(session, id=104)
        u3 = await worker_db.add_user(session, id=105)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000004"
        )
        await worker_db.add_membership(
            session, user_id=u1.id, habit_id=habit.id  # active
        )
        await worker_db.add_membership(
            session,
            user_id=u2.id,
            habit_id=habit.id,
            status=MembershipStatus.PAUSED,
        )
        await worker_db.add_membership(
            session,
            user_id=u3.id,
            habit_id=habit.id,
            status=MembershipStatus.LEFT,
        )
        await session.commit()

    result = await _process()
    assert result["summary"] == [{"habit_id": habit.id, "penalized": 1}]

    async with worker_db.session_factory() as session:
        penalties = (await session.execute(select(Penalty))).scalars().all()
        assert len(penalties) == 1


@pytest.mark.asyncio
async def test_close_window_skips_inactive_habit(worker_db):
    """is_active=False → привычка не обрабатывается."""
    from sqlalchemy import select

    from app.models.penalty import Penalty
    from worker.tasks.close_catch_window import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=106)
        habit = await worker_db.add_habit(
            session,
            id="00000000-0000-0000-0000-000000000005",
            is_active=False,
        )
        await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await session.commit()

    result = await _process()
    assert result["summary"] == []

    async with worker_db.session_factory() as session:
        penalties = (await session.execute(select(Penalty))).scalars().all()
        assert penalties == []


@pytest.mark.asyncio
async def test_close_window_pauses_membership_when_deposit_zero(worker_db):
    """Если депозит = 0 и есть пропуск → membership переходит в paused."""
    from sqlalchemy import select

    from app.core.constants import MembershipStatus
    from app.models.membership import Membership
    from worker.tasks.close_catch_window import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=107)
        habit = await worker_db.add_habit(
            session,
            id="00000000-0000-0000-0000-000000000006",
            penalty_amount=100,
        )
        await worker_db.add_membership(
            session,
            user_id=user.id,
            habit_id=habit.id,
            deposit_balance=0,
        )
        await session.commit()

    await _process()

    async with worker_db.session_factory() as session:
        m = (
            await session.execute(select(Membership))
        ).scalars().one()
        assert m.status == MembershipStatus.PAUSED
