"""Тесты для worker-задачи `apply_catch_bonus`.

Идемпотентный начислитель +1 охотнику за успешный улов. Идемпотентность —
через флаг `penalty.bonus_applied` (см. docs/06 §5.3).
"""
from __future__ import annotations

from datetime import date

import pytest


@pytest.mark.asyncio
async def test_apply_bonus_increments_user_points(worker_db):
    """Первый запуск после catch: bonus_points пользователя +1, penalty.bonus_applied=True."""
    from sqlalchemy import select

    from app.core.constants import PenaltyConfig
    from app.models.user import User
    from worker.tasks.apply_catch_bonus import run

    async with worker_db.session_factory() as session:
        catcher_user = await worker_db.add_user(session, id=400, bonus_points=0)
        violator_user = await worker_db.add_user(session, id=401, bonus_points=0)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000dd1"
        )
        catcher_m = await worker_db.add_membership(
            session, user_id=catcher_user.id, habit_id=habit.id
        )
        violator_m = await worker_db.add_membership(
            session, user_id=violator_user.id, habit_id=habit.id
        )
        penalty = await worker_db.add_penalty(
            session,
            violator_membership_id=violator_m.id,
            catcher_membership_id=catcher_m.id,
            amount=100,
            reason="caught",
            on_date=date(2026, 7, 21),
            bonus_applied=False,
        )
        await session.commit()
        penalty_id = penalty.id

    result = await run(
        {
            "catcher_membership_id": catcher_m.id,
            "penalty_id": penalty_id,
        }
    )

    assert result["ok"] is True
    assert result["applied"] == PenaltyConfig.CATCHER_BONUS_POINTS

    async with worker_db.session_factory() as session:
        u = (
            await session.execute(select(User).where(User.id == 400))
        ).scalar_one()
        assert u.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS


@pytest.mark.asyncio
async def test_apply_bonus_idempotent_second_call(worker_db):
    """Повторный запуск по той же penalty → no-op, bonus не удваивается."""
    from sqlalchemy import select

    from app.core.constants import PenaltyConfig
    from app.models.user import User
    from app.models.penalty import Penalty
    from worker.tasks.apply_catch_bonus import run

    async with worker_db.session_factory() as session:
        catcher = await worker_db.add_user(session, id=410, bonus_points=0)
        violator = await worker_db.add_user(session, id=411)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000dd2"
        )
        cm = await worker_db.add_membership(
            session, user_id=catcher.id, habit_id=habit.id
        )
        vm = await worker_db.add_membership(
            session, user_id=violator.id, habit_id=habit.id
        )
        p = await worker_db.add_penalty(
            session,
            violator_membership_id=vm.id,
            catcher_membership_id=cm.id,
            amount=100,
            reason="caught",
            on_date=date(2026, 7, 21),
            bonus_applied=False,
        )
        await session.commit()
        penalty_id = p.id

    first = await run({"catcher_membership_id": cm.id, "penalty_id": penalty_id})
    second = await run({"catcher_membership_id": cm.id, "penalty_id": penalty_id})

    assert first["applied"] == PenaltyConfig.CATCHER_BONUS_POINTS
    assert second["applied"] == 0  # идемпотентно

    async with worker_db.session_factory() as session:
        u = (
            await session.execute(select(User).where(User.id == 410))
        ).scalar_one()
        assert u.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS

        p_obj = (
            await session.execute(select(Penalty).where(Penalty.id == penalty_id))
        ).scalar_one()
        assert p_obj.bonus_applied is True


@pytest.mark.asyncio
async def test_apply_bonus_returns_zero_when_no_catcher(worker_db):
    """Если penalty без catcher (window_closed_no_catch) → бонус не начисляется."""
    from worker.tasks.apply_catch_bonus import run

    async with worker_db.session_factory() as session:
        violator = await worker_db.add_user(session, id=420)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000dd3"
        )
        vm = await worker_db.add_membership(
            session, user_id=violator.id, habit_id=habit.id
        )
        p = await worker_db.add_penalty(
            session,
            violator_membership_id=vm.id,
            catcher_membership_id=None,
            amount=100,
            reason="window_closed_no_catch",
            on_date=date(2026, 7, 21),
            bonus_applied=False,
        )
        await session.commit()
        penalty_id = p.id

    result = await run({"catcher_membership_id": vm.id, "penalty_id": penalty_id})
    assert result["ok"] is True
    assert result["applied"] == 0
