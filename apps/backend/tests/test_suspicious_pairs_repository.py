"""Тесты SuspiciousPairsRepository.lookup_flagged.

T2: метод используется PenaltyService вместо приватного _is_suspicious.
Покрываем: пары 'flagged' → True; 'banned' / нет строки → False; a == b → False;
канонический порядок (A,B) ≡ (B,A).
"""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.auxiliary import SuspiciousPair
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SuspiciousPair.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _add_pair(session: AsyncSession, a: str, b: str, status: str) -> None:
    ca, cb = (a, b) if a <= b else (b, a)
    session.add(
        SuspiciousPair(
            membership_id_a=ca,
            membership_id_b=cb,
            status=status,
            reason="test",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_lookup_flagged_true_for_flagged_pair(session: AsyncSession) -> None:
    a, b = str(uuid4()), str(uuid4())
    await _add_pair(session, a, b, status="flagged")
    await session.commit()

    repo = SuspiciousPairsRepository(session)
    assert await repo.lookup_flagged(a, b) is True


@pytest.mark.asyncio
async def test_lookup_flagged_false_for_banned_pair(session: AsyncSession) -> None:
    """Пара в статусе banned не блокирует catcher_bonus — только 'flagged'.

    В бан-режиме гейт ловить нельзя вообще (другая механика), но для этой
    проверки важно, что lookup_flagged не путает banned и flagged.
    """
    a, b = str(uuid4()), str(uuid4())
    await _add_pair(session, a, b, status="banned")
    await session.commit()

    repo = SuspiciousPairsRepository(session)
    assert await repo.lookup_flagged(a, b) is False


@pytest.mark.asyncio
async def test_lookup_flagged_false_for_missing_pair(session: AsyncSession) -> None:
    repo = SuspiciousPairsRepository(session)
    assert await repo.lookup_flagged(str(uuid4()), str(uuid4())) is False


@pytest.mark.asyncio
async def test_lookup_flagged_false_when_a_equals_b(session: AsyncSession) -> None:
    """a == b — ловить самого себя запрещено, _is_suspicious защитный возврат False.
    Lookup тоже должен вернуть False (без запроса в БД).
    """
    repo = SuspiciousPairsRepository(session)
    same = str(uuid4())
    assert await repo.lookup_flagged(same, same) is False


@pytest.mark.asyncio
async def test_lookup_flagged_is_canonical_order_agnostic(session: AsyncSession) -> None:
    """Канонический ключ — упорядоченная пара: (A,B) и (B,A) дают один ответ."""
    a, b = str(uuid4()), str(uuid4())
    assert a != b

    ca, cb = min(a, b), max(a, b)
    # Сохраняем с явной канонизацией вручную — имитируем прод-флаг.
    session.add(
        SuspiciousPair(
            membership_id_a=ca,
            membership_id_b=cb,
            status="flagged",
            reason="test",
        )
    )
    await session.commit()

    repo = SuspiciousPairsRepository(session)
    assert await repo.lookup_flagged(a, b) is True
    assert await repo.lookup_flagged(b, a) is True
