"""Тесты для internal endpoint /internal/bot/habit_state (PR №9).

Бот запрашивает это ДО отправки чек-ина, чтобы:
- отвергнуть неподдерживаемый proof_type (pre-filter);
- отвергнуть повторный чек-ин за сегодня (pre-filter).

Auth: X-Service-Token (как у всех /internal/*).

Используем SQLite + StaticPool и паттерн подмены Postgres-функций
из test_admin_habits_api.py / apps/worker/tests/conftest.py.
"""
from __future__ import annotations

import os

# --- Postgres → SQLite compatibility (тот же паттерн, что в test_admin_habits_api.py) ---
import re  # noqa: E402
import tempfile
import uuid
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.compiler import SQLCompiler  # noqa: E402

from app.core.config import get_settings
from app.core.security import generate_service_token
from app.db import session as session_module
from app.main import create_app
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return "'00000000-0000-0000-0000-000000000000'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _rewrite_sql_for_sqlite(statement: str, parameters, _uuid_seq: list[int]):
    def _repl_uuid(_m: re.Match) -> str:
        _uuid_seq[0] += 1
        return f"'{uuid.uuid4()}'"

    def _repl_now(_m: re.Match) -> str:
        return "CURRENT_TIMESTAMP"

    def _repl_date(_m: re.Match) -> str:
        return "CURRENT_DATE"

    statement = re.sub(r"gen_random_uuid\s*\(\s*\)", _repl_uuid, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bnow\s*\(\s*\)", _repl_now, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bcurrent_date\b", _repl_date, statement, flags=re.IGNORECASE)
    return statement, parameters


def _remap_postgres_types_for_sqlite() -> None:
    from sqlalchemy import JSON, String
    from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

    for m in (User, Habit, Membership, Checkin):
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)


_remap_postgres_types_for_sqlite()


# --- fixtures ---------------------------------------------------------------


SERVICE_SECRET = "test-service-secret"


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "0")  # owner-gate off
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", SERVICE_SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_habit_state_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    _uuid_seq: list[int] = [0]

    @_sa_event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def _patch_sql(conn, cursor, statement, parameters, context, executemany):
        return _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq)

    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(Habit.__table__.create)
        await conn.run_sync(Membership.__table__.create)
        await conn.run_sync(Checkin.__table__.create)
        # Pravki-bug-fixes §Z-21 (Item 4): HabitStateResponse читает
        # penalties (через PenaltyRepository.has_any_penalty_today), поэтому
        # таблица обязательна для теста. Иначе sqlite3.OperationalError.
        await conn.run_sync(Penalty.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine  # noqa: SLF001
    session_module._session_factory = factory  # noqa: SLF001

    yield engine

    await engine.dispose()
    session_module._engine = None  # noqa: SLF001
    session_module._session_factory = None  # noqa: SLF001
    os.unlink(db_path)
    os.rmdir(tmp_dir)
    get_settings.cache_clear()


@pytest.fixture
def app(_sqlite_engine: Any):
    return create_app()


@pytest.fixture
def service_token() -> str:
    return generate_service_token(
        service_name="bot",
        target_audience="backend-api",
        secret=SERVICE_SECRET,
        ttl_seconds=60,
    )


async def _make_user(s, telegram_id: int) -> User:
    u = User(id=telegram_id, first_name="Probe", username=None)
    s.add(u)
    await s.flush()
    return u


async def _make_habit(
    s,
    *,
    chat_id: int = -1004348250990,
    title: str = "Пробежка",
    proof_types: list[str] | None = None,
    timezone: str = "Europe/Moscow",
) -> Habit:
    from app.core.constants import ProofType

    pt = proof_types or ["video_note"]
    h = Habit(
        title=title,
        description=title,
        chat_id=chat_id,
        checkin_window_start=dt_time(0, 0),  # всегда открыто для тестов
        checkin_window_end=dt_time(23, 59, 59),
        timezone=timezone,
        penalty_amount=100_00,
        price_month=100_00,
        proof_type=ProofType(pt[0]),
        proof_types=pt,
        prize_pool=0,
        is_active=True,
        chat_topic_thread_id=None,
        checkin_topic_thread_id=12,
    )
    s.add(h)
    await s.flush()
    return h


async def _make_membership(s, *, user_id: int, habit_id: Any) -> Membership:
    m = Membership(user_id=user_id, habit_id=habit_id, status="active")
    s.add(m)
    await s.flush()
    return m


@pytest_asyncio.fixture
async def setup_basic(_sqlite_engine: Any):
    """Создаёт user + habit (Пробежка, video_note only) + membership."""
    async with session_module._session_factory() as s:
        user = await _make_user(s, 7295309649)
        habit = await _make_habit(s, proof_types=["video_note"])
        await _make_membership(s, user_id=user.id, habit_id=habit.id)
        await s.commit()
    return {"chat_id": habit.chat_id, "user_id": user.id, "habit_id": str(habit.id)}


# --- tests ------------------------------------------------------------------


class TestHabitStateEndpoint:
    def test_habit_not_found_returns_found_false(
        self, app: Any, service_token: str
    ) -> None:
        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={"chat_id": -1000000000000, "user_id": 1},
                headers={"X-Service-Token": service_token},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is False
        assert body["habit_id"] is None
        assert body["proof_types"] == []
        assert body["already_checked_in"] is False

    def test_habit_found_no_checkin(
        self, app: Any, service_token: str, setup_basic: dict[str, Any]
    ) -> None:
        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={
                    "chat_id": setup_basic["chat_id"],
                    "user_id": setup_basic["user_id"],
                },
                headers={"X-Service-Token": service_token},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["habit_id"] == setup_basic["habit_id"]
        assert body["proof_types"] == ["video_note"]
        assert body["checkin_topic_thread_id"] == 12
        assert body["already_checked_in"] is False
        assert body["checked_in_at"] is None

    def test_habit_found_already_checked_in(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Создаёт habit + user + membership + checkin на сегодня → already_checked_in=True."""
        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s, chat_id=-1004348250990, proof_types=["video_note"]
                )
                m = await _make_membership(s, user_id=user.id, habit_id=habit.id)
                # Чек-ин на "сегодня" в TZ клуба.
                now_utc = datetime.now(tz=UTC)
                club_today = habit.club_date(now_utc)
                ci = Checkin(
                    membership_id=m.id,
                    date=club_today,
                    status="done",
                    proof_message_id=12345,
                    verified_at=now_utc,
                )
                s.add(ci)
                await s.commit()
                return habit.chat_id, user.id

        # Запускаем в уже существующем loop через движок.
        import asyncio

        chat_id, user_id = asyncio.run(_seed())

        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={"chat_id": chat_id, "user_id": user_id},
                headers={"X-Service-Token": service_token},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["already_checked_in"] is True
        assert body["checked_in_at"] is not None

    def test_habit_multi_proof_types(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Клуб с 2 типами proof → proof_types возвращается полностью."""
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s, chat_id=-1004467477629, proof_types=["video_note", "photo"]
                )
                await _make_membership(s, user_id=user.id, habit_id=habit.id)
                await s.commit()
                return habit.chat_id, user.id

        chat_id, user_id = asyncio.run(_seed())

        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={"chat_id": chat_id, "user_id": user_id},
                headers={"X-Service-Token": service_token},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert set(body["proof_types"]) == {"video_note", "photo"}

    def test_user_without_membership(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """user есть, membership нет → found=True, already_checked_in=False."""
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 111222333)
                habit = await _make_habit(s, proof_types=["video_note"])
                # намеренно НЕ создаём membership
                await s.commit()
                return habit.chat_id, user.id

        chat_id, user_id = asyncio.run(_seed())

        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={"chat_id": chat_id, "user_id": user_id},
                headers={"X-Service-Token": service_token},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is True
        assert body["already_checked_in"] is False

    def test_missing_service_token_returns_401(self, app: Any) -> None:
        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={"chat_id": -1004348250990, "user_id": 7295309649},
            )
        assert r.status_code == 401
        assert r.json()["code"] in ("missing_service_token", "invalid_service_token")

    def test_missing_query_params_returns_422(
        self, app: Any, service_token: str
    ) -> None:
        with TestClient(app) as client:
            r = client.get(
                "/internal/bot/habit_state",
                params={"chat_id": -1004348250990},
                headers={"X-Service-Token": service_token},
            )
        assert r.status_code == 422
