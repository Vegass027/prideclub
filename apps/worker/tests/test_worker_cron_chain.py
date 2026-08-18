"""End-to-end тест worker-конвейера.

Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 2):
переписан под новую механику — авто-списание отключено.

Сценарий:
- Два юзера пропустили несколько клуб-дней подряд без чек-инов.
- close_catch_window НЕ создаёт Penalty / Transaction, не меняет deposit.
- Checkin.missed для каждого пропущенного дня; recompute_pause_status
  синхронизирует PA с депозитом (если deposit < penalty → PAUSED).
"""
from __future__ import annotations

from unittest.mock import patch
from datetime import datetime, timezone

import pytest

@pytest.mark.asyncio
async def test_close_catch_window_pauses_after_miss_with_low_deposit(
    worker_db,
) -> None:
    """Шаг 3 (Commit 2): после пропусков deposit<pentalty → PAUSED.

    Тот же юзер (id=700, deposit=50) в клубе с penalty=100. Один прогон
    cron → Checkin.missed за housekeeping_club_date + PAUSED.
    """
    from sqlalchemy import select

    from app.core.constants import MembershipStatus
    from app.models.membership import Membership
    from app.models.user import User
    from worker.tasks.close_catch_window import _process as run_for_active_habits

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=700)
        user.deposit_balance = 50  # < penalty=100
        await session.flush()

        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-0000000000aa"
        )
        await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await session.commit()

    # 19 aug 04:05 UTC = 07:05 MSK 19 aug → housekeeping для 18 aug.
    now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
    with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
        mock_dt.now.return_value = now_utc
        await run_for_active_habits()

    async with worker_db.session_factory() as session:
        m = (await session.execute(
            select(Membership).where(Membership.user_id == 700)
        )).scalar_one()
        u = (await session.execute(
            select(User).where(User.id == 700)
        )).scalar_one()
        # recompute_pause_status под user-lock: deposit(50) < penalty(100) → PAUSED.
        assert m.status == MembershipStatus.PAUSED
        assert u.deposit_balance == 50, "deposit не меняется"