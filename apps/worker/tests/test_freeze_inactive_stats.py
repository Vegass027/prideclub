"""Functional tests для freeze-inactive-stats worker (Phase 3 Task 3.5).

⚠️ Functional SQLite — НЕ concurrency-тест. Реальные PostgreSQL race-
тесты (двухсессионный фриз с реальным FOR UPDATE) отложены в отдельную
будущую задачу; требуют PG test infra. Все 3 теста подтверждают
batch-loop корректность, особенно защиту от OFFSET-пропуска.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.stat_definition import StatDefinition
from app.models.user import User
from app.models.user_stats import UserStats
from worker.tasks.freeze_inactive_stats import _process


async def _make_inactive_stats(
    session, *, count: int, last_checkin_offset_days: int = 31,
) -> None:
    """Bulk-seed count inactive user_stats с обязательными parent FK rows.

    Audit User NOT NULL полей (Phase 3 Task 3.5, per Dmitry 21.08.2026):
    - id (BigInteger PK без autoincrement) → ОБЯЗАТЕЛЬНО explicit.
    - first_name (NOT NULL, NO default) → ОБЯЗАТЕЛЬНО explicit.
    - timezone, deposit_balance, notifications_enabled, data_anonymized,
      created_at (NOT NULL + server_default) → autofill через SQL
      DEFAULT clause (verified по existing add_user в conftest).

    Audit FK constraints:
    - UserStats.user_id → users.id → seed users.
    - UserStats.stat_definition_id → stat_definitions.id → seed 8 SD.
    """
    last_checkin = (
        datetime.now(tz=timezone.utc) - timedelta(days=last_checkin_offset_days)
    )

    # 1. StatDefinitions (8 шт — для UserStats.stat_definition_id = "sd-test-{i%8}").
    for i in range(8):
        session.add(
            StatDefinition(
                id=f"sd-test-{i}",
                slug=f"slug-{i}",
                name=f"Stat {i}",
                icon="⚡",
                sort_order=i,
                is_active=True,
            )
        )

    # 2. Users (count шт).
    # Минимум обязательных: id (PK без autoincrement) + first_name.
    # Все NOT NULL поля с server_default'ами autofill при bulk-insert.
    session.add_all([
        User(id=1000 + i, first_name=f"u{i}")
        for i in range(count)
    ])

    # 3. UserStats (count шт, alternating через 8 stat_definitions).
    session.add_all([
        UserStats(
            id=str(uuid4()),
            user_id=1000 + i,
            stat_definition_id=f"sd-test-{i % 8}",
            value=0,
            last_checkin_at=last_checkin,
            is_frozen=False,
        )
        for i in range(count)
    ])

    await session.commit()


@pytest.mark.asyncio
async def test_freeze_empty_db_zero_batches_zero_frozen(worker_db):
    """Пустая БД → 0 batches, 0 frozen.

    НЕ assert'им «no SQL» — это требует instrumentation session/repository
    и не имеет смысла без явной проверки. Тест покрывает только
    корректный result при empty input.
    """
    _, factory = worker_db.engine, worker_db.session_factory

    result = await _process(session_factory=factory)

    assert result == {
        "batches": 0,
        "frozen_total": 0,
        "threshold_days": 30,
        "batch_size": 1000,
    }


@pytest.mark.asyncio
async def test_freeze_five_rows_one_batch_all_frozen(worker_db):
    """5 inactive rows → один batch (batch=1000), все 5 frozen."""
    _, factory = worker_db.engine, worker_db.session_factory

    async with factory() as session:
        await _make_inactive_stats(session, count=5)

    result = await _process(session_factory=factory)

    assert result["batches"] == 1
    assert result["frozen_total"] == 5

    # verify all 5 are now is_frozen=True.
    async with factory() as session:
        count_frozen = await session.scalar(
            select(func.count())
            .select_from(UserStats)
            .where(UserStats.is_frozen.is_(True))
        )
    assert count_frozen == 5


@pytest.mark.asyncio
async def test_freeze_2001_rows_three_batches_no_residual(worker_db):
    """Защита от OFFSET-skipped rows (Task 3.2 fix 1).

    2001 inactive stats → 3 batches (1000 + 1000 + 1) → все 2001 frozen,
    0 residual unfrozen. OFFSET-skip регрессия дала бы
    frozen_total < 2001.
    """
    _, factory = worker_db.engine, worker_db.session_factory

    async with factory() as session:
        await _make_inactive_stats(session, count=2001)

    result = await _process(session_factory=factory)

    assert result["batches"] == 3, (
        f"Ожидали 3 батча (2001/1000 = 2 полных + 1 остаток). "
        f"OFFSET-skip дал бы 1."
    )
    assert result["frozen_total"] == 2001, (
        f"Ожидали 2001 frozen, получили {result['frozen_total']}. "
        f"OFFSET-skip потерял бы ~2000 rows."
    )

    async with factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(UserStats)
        )
        frozen = await session.scalar(
            select(func.count())
            .select_from(UserStats)
            .where(UserStats.is_frozen.is_(True))
        )
        not_frozen = await session.scalar(
            select(func.count())
            .select_from(UserStats)
            .where(UserStats.is_frozen.is_(False))
        )
    assert total == 2001
    assert frozen == 2001
    assert not_frozen == 0
