"""Тесты для SuspiciousPairsService на реальной in-memory SQLite-БД.

Поднимаем настоящий AsyncSession поверх aiosqlite с реальными таблицами
penalties / suspicious_pairs и прогоняем фактические SELECT/INSERT — ровно
как в проде на Postgres, только без asyncpg и сети.

По docs SQLAlchemy: in-memory SQLite с async требует StaticPool, чтобы
разные корутины не дрались за одну транзакцию.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.constants import PenaltyConfig, PenaltyReason, SuspiciousPairStatus
from app.models.auxiliary import SuspiciousPair
from app.models.penalty import Penalty
from app.services.suspicious_pairs_service import SuspiciousPairsService


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """AsyncSession на in-memory SQLite с реальными таблицами.

    - StaticPool — все корутины делят одну connection в памяти.
    - SQLite игнорирует server_default `func.gen_random_uuid()` / `now()` /
      `current_date()` (Postgres-функции) — поэтому id/timestamps проставляем
      явно в INSERT'ах, как в проде через backend-сервисы.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Penalty.__table__.create)
        await conn.run_sync(SuspiciousPair.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s

    await engine.dispose()


async def _add_penalty(
    session: AsyncSession,
    *,
    catcher_membership_id: str,
    violator_membership_id: str,
    on_date: date,
) -> None:
    """Создаёт запись о штрафе: catcher поймал violator."""
    session.add(
        Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=catcher_membership_id,
            amount=100,
            fund_share=100,
            reason=PenaltyReason.CAUGHT.value,
            date=on_date,
            bonus_applied=False,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_asymmetry_threshold_flags_pair(session: AsyncSession) -> None:
    """catcher ≥ N раз поймал violator, а тот ни разу — пара флагуется."""
    catcher = str(uuid4())
    violator = str(uuid4())
    today = date(2026, 1, 15)

    for i in range(PenaltyConfig.SUSPICIOUS_ASYMMETRY_THRESHOLD):
        await _add_penalty(
            session,
            catcher_membership_id=catcher,
            violator_membership_id=violator,
            on_date=today - timedelta(days=i),
        )
    await session.commit()

    service = SuspiciousPairsService(session)
    suspicious, reason = await service.evaluate_after_catch(
        catcher_membership_id=catcher,
        violator_membership_id=violator,
        club_date=today,
    )

    assert suspicious is True
    assert reason is not None
    assert "asymmetry" in reason

    pair = await service._repo.get(catcher, violator)  # noqa: SLF001
    assert pair is not None
    assert pair.status == SuspiciousPairStatus.FLAGGED.value
    # Канонический ключ: упорядоченная пара.
    assert (pair.membership_id_a, pair.membership_id_b) == (
        min(catcher, violator),
        max(catcher, violator),
    )


@pytest.mark.asyncio
async def test_reciprocal_catches_do_not_flag(session: AsyncSession) -> None:
    """Обе стороны ловят друг друга одинаково — флага нет."""
    a = str(uuid4())
    b = str(uuid4())
    today = date(2026, 1, 15)

    for i in range(PenaltyConfig.SUSPICIOUS_ASYMMETRY_THRESHOLD):
        await _add_penalty(
            session,
            catcher_membership_id=a,
            violator_membership_id=b,
            on_date=today - timedelta(days=i),
        )
        await _add_penalty(
            session,
            catcher_membership_id=b,
            violator_membership_id=a,
            on_date=today - timedelta(days=i),
        )
    await session.commit()

    service = SuspiciousPairsService(session)
    suspicious, reason = await service.evaluate_after_catch(
        catcher_membership_id=a,
        violator_membership_id=b,
        club_date=today,
    )

    assert suspicious is False
    assert reason is None
    assert await service._repo.get(a, b) is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_below_threshold_not_flagged(session: AsyncSession) -> None:
    """2 лова в одну сторону и 0 в обратную — ниже порога."""
    catcher = str(uuid4())
    violator = str(uuid4())
    today = date(2026, 1, 15)

    for i in range(PenaltyConfig.SUSPICIOUS_ASYMMETRY_THRESHOLD - 1):
        await _add_penalty(
            session,
            catcher_membership_id=catcher,
            violator_membership_id=violator,
            on_date=today - timedelta(days=i),
        )
    await session.commit()

    service = SuspiciousPairsService(session)
    suspicious, _ = await service.evaluate_after_catch(
        catcher_membership_id=catcher,
        violator_membership_id=violator,
        club_date=today,
    )

    assert suspicious is False


@pytest.mark.asyncio
async def test_is_blocked_for_bonus_returns_true_after_flag(
    session: AsyncSession,
) -> None:
    """После флага is_blocked_for_bonus → True; бонус НЕ начислится."""
    a = str(uuid4())
    b = str(uuid4())
    today = date(2026, 1, 15)

    for i in range(PenaltyConfig.SUSPICIOUS_ASYMMETRY_THRESHOLD):
        await _add_penalty(
            session,
            catcher_membership_id=a,
            violator_membership_id=b,
            on_date=today - timedelta(days=i),
        )
    await session.commit()

    service = SuspiciousPairsService(session)
    await service.evaluate_after_catch(
        catcher_membership_id=a, violator_membership_id=b, club_date=today
    )

    assert await service.is_blocked_for_bonus(a, b) is True


@pytest.mark.asyncio
async def test_canonical_key_invariant(session: AsyncSession) -> None:
    """(A,B) и (B,A) дают один и тот же канонический ключ."""
    a = "a" * 32 + "0000"  # 36 chars
    b = "b" * 32 + "0000"
    assert len(a) == len(b) == 36

    service = SuspiciousPairsService(session)
    assert service._repo._canonical(a, b) == (a, b)  # noqa: SLF001
    assert service._repo._canonical(b, a) == (a, b)  # noqa: SLF001


@pytest.mark.asyncio
async def test_flag_idempotent_second_call(session: AsyncSession) -> None:
    """Повторный evaluate_after_catch не дублирует строку — остаётся одна."""
    catcher = str(uuid4())
    violator = str(uuid4())
    today = date(2026, 1, 15)

    for i in range(PenaltyConfig.SUSPICIOUS_ASYMMETRY_THRESHOLD):
        await _add_penalty(
            session,
            catcher_membership_id=catcher,
            violator_membership_id=violator,
            on_date=today - timedelta(days=i),
        )
    await session.commit()

    service = SuspiciousPairsService(session)
    first, _ = await service.evaluate_after_catch(
        catcher_membership_id=catcher,
        violator_membership_id=violator,
        club_date=today,
    )
    second, _ = await service.evaluate_after_catch(
        catcher_membership_id=catcher,
        violator_membership_id=violator,
        club_date=today,
    )

    assert first is True
    assert second is True  # short-circuit: existing flag → True

    # Записей в suspicious_pairs осталась ровно одна (PK-пара).
    rows = (
        await session.execute(SuspiciousPair.__table__.select())
    ).fetchall()
    assert len(rows) == 1