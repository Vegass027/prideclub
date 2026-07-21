"""Тест гейтов is_active/archived в публичных роутах (TZ §3.6.6).

Проверяем что:
- POST /api/v1/habits/{id}/join для архивного → 404 habit_archived
- POST /api/v1/habits/{id}/join для неактивного → 409 habit_inactive
- GET /api/v1/habits/{id}/members для архивного → 404 habit_archived
- GET /api/v1/habits/{id}/today для архивного → 404 habit_archived

Используем SQLite + NullPool + подмену Postgres-функций
(паттерн из apps/worker/tests/conftest.py).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.constants import MembershipStatus, ProofType
from app.db import session as session_module
from app.main import create_app
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User


# --- Postgres → SQLite shims (из apps/worker/tests/conftest.py) ---------


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return "'00000000-0000-0000-0000-000000000000'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


from sqlalchemy.sql.compiler import SQLCompiler  # noqa: E402

SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq):
    import re

    def _repl_uuid(_m):
        _uuid_seq[0] += 1
        return f"'{uuid.uuid4()}'"

    def _repl_now(_m):
        return "CURRENT_TIMESTAMP"

    def _repl_date(_m):
        return "CURRENT_DATE"

    statement = re.sub(r"gen_random_uuid\s*\(\s*\)", _repl_uuid, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bnow\s*\(\s*\)", _repl_now, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bcurrent_date\b", _repl_date, statement, flags=re.IGNORECASE)
    return statement, parameters


def _remap_postgres_types_for_sqlite() -> None:
    from sqlalchemy import JSON, String
    from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

    models = [User, Habit, Membership, Checkin]
    for m in models:
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)


_remap_postgres_types_for_sqlite()


# --- helpers --------------------------------------------------------------


def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
    user = {
        "id": user_id,
        "first_name": "User",
        "username": "u",
    }
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "q",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


# --- fixtures --------------------------------------------------------------


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_gates_test_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    _uuid_seq: list[int] = [0]

    @_sa_event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def _patch(conn, cursor, statement, parameters, context, executemany):
        return _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq)

    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(Habit.__table__.create)
        await conn.run_sync(Membership.__table__.create)
        await conn.run_sync(Checkin.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine  # noqa: SLF001
    session_module._session_factory = factory  # noqa: SLF001

    yield engine, factory

    await engine.dispose()
    session_module._engine = None  # noqa: SLF001
    session_module._session_factory = None  # noqa: SLF001
    os.unlink(db_path)
    os.rmdir(tmp_dir)
    get_settings.cache_clear()


@pytest.fixture
def app(_sqlite_engine: Any):
    _, _ = _sqlite_engine
    return create_app()


async def _seed_habit(
    factory: async_sessionmaker[AsyncSession],
    *,
    is_active: bool,
    archived: bool,
    chat_id: int,
) -> str:
    habit_id = str(uuid.uuid4())
    async with factory() as s:
        habit = Habit(
            id=habit_id,
            title="Test",
            chat_id=chat_id,
            checkin_window_start=__import__("datetime").time(0, 0),
            checkin_window_end=__import__("datetime").time(23, 59),
            timezone="Europe/Moscow",
            penalty_amount=100,
            price_month=1000,
            proof_type=ProofType.VIDEO_NOTE,
            is_active=is_active,
            archived_at=(
                datetime.now(timezone.utc) if archived else None
            ),
        )
        s.add(habit)
        await s.commit()
    return habit_id


async def _seed_membership(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    habit_id: str,
) -> None:
    async with factory() as s:
        s.add(
            Membership(
                id=str(uuid.uuid4()),
                user_id=user_id,
                habit_id=habit_id,
                status=MembershipStatus.ACTIVE,
                joined_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()


async def _seed_user(factory: async_sessionmaker[AsyncSession], user_id: int) -> None:
    async with factory() as s:
        s.add(User(id=user_id, first_name="User", username="u"))
        await s.commit()


# --- tests -----------------------------------------------------------------


class TestJoinGate:
    def test_join_archived_returns_404(self, app: Any, _sqlite_engine: Any) -> None:
        _, factory = _sqlite_engine
        habit_id = asyncio_run(_seed_habit(factory, is_active=True, archived=True, chat_id=-1001))

        with TestClient(app) as client:
            r = client.post(
                f"/api/v1/habits/{habit_id}/join",
                headers={"X-Telegram-Init-Data": _build_init_data(user_id=123)},
            )
        assert r.status_code == 404
        assert r.json()["code"] == "habit_archived"

    def test_join_inactive_returns_409(self, app: Any, _sqlite_engine: Any) -> None:
        _, factory = _sqlite_engine
        habit_id = asyncio_run(_seed_habit(factory, is_active=False, archived=False, chat_id=-1002))

        with TestClient(app) as client:
            r = client.post(
                f"/api/v1/habits/{habit_id}/join",
                headers={"X-Telegram-Init-Data": _build_init_data(user_id=124)},
            )
        assert r.status_code == 409
        assert r.json()["code"] == "habit_inactive"


class TestMembersGate:
    def test_members_archived_returns_404(self, app: Any, _sqlite_engine: Any) -> None:
        _, factory = _sqlite_engine
        habit_id = asyncio_run(_seed_habit(factory, is_active=True, archived=True, chat_id=-1003))

        with TestClient(app) as client:
            r = client.get(
                f"/api/v1/habits/{habit_id}/members",
                headers={"X-Telegram-Init-Data": _build_init_data(user_id=125)},
            )
        assert r.status_code == 404
        assert r.json()["code"] == "habit_archived"


class TestTodayGate:
    def test_today_archived_returns_404(self, app: Any, _sqlite_engine: Any) -> None:
        _, factory = _sqlite_engine
        habit_id = asyncio_run(_seed_habit(factory, is_active=True, archived=True, chat_id=-1004))
        asyncio_run(_seed_user(factory, 126))
        asyncio_run(_seed_membership(factory, user_id=126, habit_id=habit_id))

        with TestClient(app) as client:
            r = client.get(
                f"/api/v1/habits/{habit_id}/today",
                headers={"X-Telegram-Init-Data": _build_init_data(user_id=126)},
            )
        assert r.status_code == 404
        assert r.json()["code"] == "habit_archived"


# --- sync helper ----------------------------------------------------------


def asyncio_run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)
