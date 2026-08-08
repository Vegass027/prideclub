"""Тесты для лимита LEADERBOARD_LIMIT=100 (Pravki.md §8.1).

Покрывает:
    - Локальный leaderboard (per-club): > 100 members → rows обрезан до 100.
    - Глобальный leaderboard: > 100 users → rows=100, total=фактическое число.
    - Клуб < 100 members → rows не обрезан, total=None.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

# STATIC_DIR обязателен ДО импорта create_app (см. test_topup.py).
os.environ["STATIC_DIR"] = tempfile.mkdtemp(prefix="hc_lb_test_static_")
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio  # noqa: E402

from sqlalchemy import JSON, String  # noqa: E402
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlalchemy.sql.compiler import SQLCompiler  # noqa: E402

from app.core.constants import MembershipStatus, ProofType  # noqa: E402
from app.models.checkin import Checkin  # noqa: E402
from app.models.habit import Habit  # noqa: E402
from app.models.membership import Membership  # noqa: E402
from app.models.penalty import Penalty  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.membership_repository import MembershipRepository  # noqa: E402

# --- Postgres → SQLite shims (ровно один раз на module load) ---


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return f"'{uuid.uuid4()}'"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]

# Remap Postgres types → SQLite-compatible.
for _m in [User, Habit, Membership, Checkin, Penalty]:
    for _col in _m.__table__.columns:
        _t = _col.type
        if isinstance(_t, UUID) and not _t.as_uuid:
            _col.type = String(36)
        elif isinstance(_t, JSONB):
            _col.type = JSON()
        elif isinstance(_t, INET):
            _col.type = String(45)


# --- helpers ---


async def _make_db():
    """Создаёт engine + factory + tables. Один event loop = один engine.

    Используем file-based SQLite вместо :memory: потому что in-memory
    SQLite + connection pool = каждое соединение получает свою БД,
    create_all создаёт таблицы только в одном из них.
    """
    import tempfile as _tf

    tmp = _tf.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    tables = [
        User.__table__,
        Habit.__table__,
        Membership.__table__,
        Checkin.__table__,
        Penalty.__table__,
    ]
    async with engine.begin() as conn:
        for tbl in tables:
            await conn.run_sync(tbl.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory, db_path


async def _seed_habit_with_checkins(
    factory: async_sessionmaker[AsyncSession],
    *,
    member_count: int,
    streak_days: int = 5,
) -> str:
    habit_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Habit(
                id=habit_id,
                title="Test Club",
                chat_id=-1001,
                checkin_window_start=datetime.now().time(),
                checkin_window_end=datetime.now().time(),
                timezone="Europe/Moscow",
                penalty_amount=100,
                price_month=1000,
                proof_type=ProofType.VIDEO_NOTE,
                proof_types=[ProofType.VIDEO_NOTE.value],
                is_active=True,
            )
        )
        for i in range(member_count):
            user_id = 10000 + i
            s.add(
                User(id=user_id, first_name=f"User{i}", username=f"u{i}")
            )
            m_id = str(uuid.uuid4())
            s.add(
                Membership(
                    id=m_id,
                    user_id=user_id,
                    habit_id=habit_id,
                    status=MembershipStatus.ACTIVE,
                    joined_at=datetime.now(tz=UTC),
                )
            )
            today = datetime.now(tz=UTC).date()
            for d_offset in range(streak_days):
                s.add(
                    Checkin(
                        id=str(uuid.uuid4()),
                        membership_id=m_id,
                        date=today - timedelta(days=d_offset),
                        status="done",
                        verified_at=datetime.now(tz=UTC),
                    )
                )
        await s.commit()
    return habit_id


async def _seed_users(
    factory: async_sessionmaker[AsyncSession],
    *,
    count: int,
    id_offset: int,
) -> None:
    async with factory() as s:
        for i in range(count):
            s.add(
                User(
                    id=id_offset + i,
                    first_name=f"U{i}",
                    username=f"u{id_offset + i}",
                )
            )
        await s.commit()


# --- tests ---


def test_local_leaderboard_truncated_to_100() -> None:
    """Клуб с 150 участниками → ответ содержит ровно 100 строк (rank 1..100)."""
    from app.api.v1.leaderboard import LEADERBOARD_LIMIT, _streak_leaderboard

    assert LEADERBOARD_LIMIT == 100, "константа должна быть 100 (Pravki.md §8.1)"

    async def _run():
        _, factory, db_path = await _make_db()
        try:
            habit_id = await _seed_habit_with_checkins(
                factory, member_count=150, streak_days=5
            )
            async with factory() as s:
                return await _streak_leaderboard(s, MembershipRepository(s), habit_id)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    rows = asyncio.run(_run())
    assert len(rows) == 100
    assert rows[0].rank == 1
    assert rows[-1].rank == 100


def test_local_leaderboard_not_truncated_for_small_club() -> None:
    """Клуб с 30 участниками → все 30 в выдаче (обрезки нет)."""
    from app.api.v1.leaderboard import _streak_leaderboard

    async def _run():
        _, factory, db_path = await _make_db()
        try:
            habit_id = await _seed_habit_with_checkins(
                factory, member_count=30, streak_days=5
            )
            async with factory() as s:
                return await _streak_leaderboard(s, MembershipRepository(s), habit_id)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    rows = asyncio.run(_run())
    assert len(rows) == 30


def test_global_leaderboard_truncated_with_total() -> None:
    """Глобальный leaderboard: 150 users → rows=100, total=150."""
    from app.api.v1.leaderboard import LEADERBOARD_LIMIT, _build_global_rows

    assert LEADERBOARD_LIMIT == 100

    async def _run():
        _, factory, db_path = await _make_db()
        try:
            await _seed_users(factory, count=150, id_offset=20000)
            metrics: dict[int, int] = {20000 + i: (150 - i) for i in range(150)}
            async with factory() as s:
                return await _build_global_rows(s, metrics)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    rows, total = asyncio.run(_run())
    assert len(rows) == 100
    assert total == 150
    assert rows[0].rank == 1
    assert rows[-1].rank == 100


def test_global_leaderboard_total_none_when_not_truncated() -> None:
    """Глобальный leaderboard: 30 users (в лимит) → rows=30, total=None."""
    from app.api.v1.leaderboard import _build_global_rows

    async def _run():
        _, factory, db_path = await _make_db()
        try:
            await _seed_users(factory, count=30, id_offset=30000)
            metrics: dict[int, int] = {30000 + i: (30 - i) for i in range(30)}
            async with factory() as s:
                return await _build_global_rows(s, metrics)
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    rows, total = asyncio.run(_run())
    assert len(rows) == 30
    assert total is None


def test_truncate_metrics_helper() -> None:
    """Чистый unit-тест: _truncate_metrics возвращает топ-K по value."""
    from app.api.v1.leaderboard import LEADERBOARD_LIMIT, _truncate_metrics

    metrics = {f"m{i}": i for i in range(LEADERBOARD_LIMIT + 50)}
    out = _truncate_metrics(metrics)
    assert len(out) == LEADERBOARD_LIMIT
    assert out[f"m{LEADERBOARD_LIMIT + 49}"] == LEADERBOARD_LIMIT + 49
    values = sorted(out.values(), reverse=True)
    assert values == sorted(set(out.values()), reverse=True)[:LEADERBOARD_LIMIT]

