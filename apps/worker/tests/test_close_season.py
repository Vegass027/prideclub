"""Тесты для worker-задачи `close_season`.

По docs/06 §7: cron `close_season` обходит активные сезоны с `ends_at <= today`,
закрывает их через `SeasonService.close_season()` (распределяет prize_pool по
`prize_rules_snapshot`).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_close_season_distributes_prize_by_snapshot_rules(worker_db):
    """Seasons с ends_at <= today → закрываются, prize_pool распределяется."""
    from app.core.constants import SeasonStatus
    from app.models.season import Season
    from app.models.transaction import Transaction
    from worker.tasks.close_season import _close_expired as _process

    today = date(2026, 7, 21)
    ends_at = today - timedelta(days=1)

    async with worker_db.session_factory() as session:
        user1 = await worker_db.add_user(session, id=500)
        user2 = await worker_db.add_user(session, id=501)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-0000000eeee1"
        )
        m1 = await worker_db.add_membership(
            session, user_id=user1.id, habit_id=habit.id
        )
        m2 = await worker_db.add_membership(
            session, user_id=user2.id, habit_id=habit.id
        )

        season = await worker_db.add_season(
            session,
            habit_id=habit.id,
            starts_at=today - timedelta(days=30),
            ends_at=ends_at,
            prize_pool=1000,
            prize_rules_snapshot={
                "rules": [
                    {"metric": "streak", "rank_from": 1, "rank_to": 2, "percentage": 100},
                ]
            },
        )

        # user1 — rank 1 (streak=10), user2 — rank 2 (streak=5)
        await worker_db.add_season_stats(
            session,
            season_id=season.id,
            membership_id=m1.id,
            streak_days=10,
        )
        await worker_db.add_season_stats(
            session,
            season_id=season.id,
            membership_id=m2.id,
            streak_days=5,
        )
        await session.commit()

    result = await _process()

    assert result == {"closed": 1}

    async with worker_db.session_factory() as session:
        s = (
            await session.execute(
                select(Season).where(Season.id == season.id)
            )
        ).scalar_one()
        assert s.status == SeasonStatus.CLOSED

        txs = (
            await session.execute(
                select(Transaction).where(
                    Transaction.type == "prize",
                    Transaction.related_membership_id.in_([m1.id, m2.id]),
                )
            )
        ).scalars().all()
        # 1000 * 100% / 2 = 500 каждому
        assert len(txs) == 2
        assert all(tx.amount == 500 for tx in txs)


@pytest.mark.asyncio
async def test_close_season_skips_active_seasons(worker_db):
    """Seasons с ends_at > today → НЕ закрываются."""
    from app.core.constants import SeasonStatus
    from app.models.season import Season
    from worker.tasks.close_season import _close_expired as _process

    today = date(2026, 7, 21)
    ends_in_future = today + timedelta(days=10)

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=510)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-0000000eeee2"
        )
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        season = await worker_db.add_season(
            session,
            habit_id=habit.id,
            starts_at=today - timedelta(days=10),
            ends_at=ends_in_future,
            prize_pool=500,
            prize_rules_snapshot={
                "rules": [
                    {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage": 100},
                ]
            },
        )
        await worker_db.add_season_stats(
            session,
            season_id=season.id,
            membership_id=m.id,
            streak_days=7,
        )
        await session.commit()

    result = await _process()
    assert result == {"closed": 0}

    async with worker_db.session_factory() as session:
        s = (
            await session.execute(
                select(Season).where(Season.id == season.id)
            )
        ).scalar_one()
        assert s.status == SeasonStatus.ACTIVE


@pytest.mark.asyncio
async def test_close_season_skips_already_closed(worker_db):
    """Seasons в статусе closed/paid_out → пропускаются."""
    from app.models.transaction import Transaction
    from worker.tasks.close_season import _close_expired as _process

    today = date(2026, 7, 21)
    ends_at = today - timedelta(days=5)

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=520)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-0000000eeee3"
        )
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        season = await worker_db.add_season(
            session,
            habit_id=habit.id,
            starts_at=today - timedelta(days=30),
            ends_at=ends_at,
            prize_pool=100,
            status="closed",
            prize_rules_snapshot={
                "rules": [
                    {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage": 100},
                ]
            },
        )
        await worker_db.add_season_stats(
            session,
            season_id=season.id,
            membership_id=m.id,
            streak_days=15,
        )
        await session.commit()

    result = await _process()
    assert result == {"closed": 0}

    async with worker_db.session_factory() as session:
        txs = (
            await session.execute(
                select(Transaction).where(Transaction.type == "prize")
            )
        ).scalars().all()
        assert txs == []


@pytest.mark.asyncio
async def test_close_season_validates_rules_sum_100_percent(worker_db):
    """Если snapshot правил некорректный (sum != 100%) → InvalidPrizeRulesError."""
    from app.core.exceptions import InvalidPrizeRulesError
    from worker.tasks.close_season import _close_expired as _process

    today = date(2026, 7, 21)
    ends_at = today - timedelta(days=1)

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=530)
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-0000000eeee4"
        )
        m = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        season = await worker_db.add_season(
            session,
            habit_id=habit.id,
            starts_at=today - timedelta(days=30),
            ends_at=ends_at,
            prize_pool=1000,
            # 50% вместо 100% — невалидный snapshot
            prize_rules_snapshot={
                "rules": [
                    {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage": 50},
                ]
            },
        )
        await worker_db.add_season_stats(
            session,
            season_id=season.id,
            membership_id=m.id,
            streak_days=10,
        )
        await session.commit()

    with pytest.raises(InvalidPrizeRulesError):
        await _process()
