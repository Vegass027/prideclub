"""Тесты для worker-задачи `expire_bonus_points`.

По docs/06-data-model §6: bonus_points сгорают через 90 дней неактивности.
Cron `expire_bonus_points` обнуляет bonus_points и bonus_points_updated_at
у всех пользователей, чьи `bonus_points_updated_at < cutoff`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_expire_zeroes_old_bonus_points(worker_db):
    """Пользователь с bonus_points_updated_at старше 90 дней → обнуляется."""
    from worker.tasks.expire_bonus_points import _process

    now = datetime.now(tz=timezone.utc)
    stale = now - timedelta(days=91)

    async with worker_db.session_factory() as session:
        await worker_db.add_user(
            session,
            id=200,
            bonus_points=50,
            bonus_points_updated_at=stale,
        )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)

    assert result == {"expired": 1}

    async with worker_db.session_factory() as session:
        from app.models.user import User

        u = (
            await session.execute(select(User).where(User.id == 200))
        ).scalar_one()
        assert u.bonus_points == 0
        assert u.bonus_points_updated_at is None


@pytest.mark.asyncio
async def test_expire_keeps_fresh_bonus_points(worker_db):
    """Пользователь с свежим bonus_points_updated_at → НЕ трогается."""
    from worker.tasks.expire_bonus_points import _process

    now = datetime.now(tz=timezone.utc)
    fresh = now - timedelta(days=10)

    async with worker_db.session_factory() as session:
        await worker_db.add_user(
            session,
            id=201,
            bonus_points=30,
            bonus_points_updated_at=fresh,
        )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)

    assert result == {"expired": 0}

    async with worker_db.session_factory() as session:
        from app.models.user import User

        u = (
            await session.execute(select(User).where(User.id == 201))
        ).scalar_one()
        assert u.bonus_points == 30
        # SQLite не сохраняет tzinfo в DateTime — сравниваем naive-эквивалент.
        assert u.bonus_points_updated_at is not None
        assert u.bonus_points_updated_at.replace(tzinfo=timezone.utc) == fresh


@pytest.mark.asyncio
async def test_expire_skips_users_with_zero_bonus(worker_db):
    """bonus_points = 0 → не считается за stale, даже если updated_at старый."""
    from worker.tasks.expire_bonus_points import _process

    now = datetime.now(tz=timezone.utc)
    stale = now - timedelta(days=200)

    async with worker_db.session_factory() as session:
        await worker_db.add_user(
            session,
            id=202,
            bonus_points=0,
            bonus_points_updated_at=stale,
        )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)
    assert result == {"expired": 0}


@pytest.mark.asyncio
async def test_expire_handles_mixed_batch(worker_db):
    """Смесь пользователей: stale+fresh, fresh+zero, stale+active — корректный счёт."""
    from worker.tasks.expire_bonus_points import _process

    now = datetime.now(tz=timezone.utc)
    stale = now - timedelta(days=91)
    fresh = now - timedelta(days=5)

    async with worker_db.session_factory() as session:
        # 3 должны сгореть
        await worker_db.add_user(
            session, id=210, bonus_points=10, bonus_points_updated_at=stale
        )
        await worker_db.add_user(
            session, id=211, bonus_points=20, bonus_points_updated_at=stale
        )
        await worker_db.add_user(
            session, id=212, bonus_points=5, bonus_points_updated_at=stale
        )
        # 3 не должны
        await worker_db.add_user(
            session, id=213, bonus_points=10, bonus_points_updated_at=fresh
        )
        await worker_db.add_user(
            session, id=214, bonus_points=0, bonus_points_updated_at=stale
        )
        await worker_db.add_user(
            session, id=215, bonus_points=10, bonus_points_updated_at=None
        )
        await session.commit()

    result = await _process(session_factory=worker_db.session_factory)
    assert result == {"expired": 3}

    async with worker_db.session_factory() as session:
        from app.models.user import User

        users = (
            await session.execute(
                select(User).where(User.id.in_([210, 211, 212, 213, 214, 215]))
            )
        ).scalars().all()
        by_id = {u.id: u for u in users}
        assert by_id[210].bonus_points == 0
        assert by_id[211].bonus_points == 0
        assert by_id[212].bonus_points == 0
        assert by_id[213].bonus_points == 10  # fresh — не тронут
        assert by_id[214].bonus_points == 0  # и так был 0
        assert by_id[215].bonus_points == 10  # updated_at is None — не считается
