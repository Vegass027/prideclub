"""Integration tests for POST /api/v1/events/stream/token.

Покрывает:
- member → 200 + JWT с правильными claims
- non-member → 403 membership_not_active
- пустой SSE_TOKEN_SECRET → 503 sse_not_configured

Использует SQLite + StaticPool (паттерн из test_admin_habits_api.py).
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
from typing import Any

import jwt as pyjwt
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.constants import MembershipStatus
from app.db import session as session_module
from app.main import create_app
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User

# --- Postgres → SQLite compatibility ----------------------------------------


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


def _rewrite_sql_for_sqlite(statement: str, parameters, _uuid_seq: list[int]):
    import re

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

    models = [User, Habit, Membership]
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


# --- helpers ----------------------------------------------------------------


def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
    user = {"id": user_id, "first_name": "Test", "username": "tester"}
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "test_query",
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


# --- fixtures ---------------------------------------------------------------


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test")
    monkeypatch.setenv("SSE_TOKEN_SECRET", "sse-test-secret")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_sse_test_")
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


@pytest_asyncio.fixture
async def habit_and_member(_sqlite_engine: Any) -> tuple[str, int]:
    """Создаёт habit + активный membership для user_id=12345.

    Возвращает (habit_id, user_id). Используется в test_member_*.
    """
    from datetime import time

    from sqlalchemy import insert

    habit_id = str(uuid.uuid4())
    user_id = 12345
    membership_id = str(uuid.uuid4())

    async with _sqlite_engine.begin() as conn:
        await conn.execute(
            insert(Habit.__table__).values(
                id=habit_id,
                title="Test",
                description="",
                chat_id=-100,
                checkin_window_start=time(0, 0),
                checkin_window_end=time(23, 59),
                timezone="Europe/Moscow",
                penalty_amount=0,
                price_month=0,
                prize_pool=0,
                is_active=True,
                proof_type="video_note",
                proof_types='["video_note"]',
            )
        )
        await conn.execute(
            insert(Membership.__table__).values(
                id=membership_id,
                user_id=user_id,
                habit_id=habit_id,
                status=MembershipStatus.ACTIVE.value,
                auto_renew_enabled=False,
            )
        )
    return habit_id, user_id


@pytest_asyncio.fixture
async def habit_and_paused_member(_sqlite_engine: Any) -> tuple[str, int]:
    """Создаёт habit + PAUSED membership для user_id=12345.

    Возвращает (habit_id, user_id). Используется в test_paused_member_*.
    """
    from datetime import time

    from sqlalchemy import insert

    habit_id = str(uuid.uuid4())
    user_id = 12345
    membership_id = str(uuid.uuid4())

    async with _sqlite_engine.begin() as conn:
        await conn.execute(
            insert(Habit.__table__).values(
                id=habit_id,
                title="T",
                description="",
                chat_id=-100,
                checkin_window_start=time(0, 0),
                checkin_window_end=time(23, 59),
                timezone="Europe/Moscow",
                penalty_amount=0,
                price_month=0,
                prize_pool=0,
                is_active=True,
                proof_type="video_note",
                proof_types='["video_note"]',
            )
        )
        await conn.execute(
            insert(Membership.__table__).values(
                id=membership_id,
                user_id=user_id,
                habit_id=habit_id,
                status=MembershipStatus.PAUSED.value,
                auto_renew_enabled=False,
            )
        )
    return habit_id, user_id


# --- tests ------------------------------------------------------------------


class TestSseTokenEndpoint:
    def test_member_gets_token(
        self, app: Any, habit_and_member: tuple[str, int]
    ) -> None:
        habit_id, user_id = habit_and_member
        init_data = _build_init_data(user_id=user_id)

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/events/stream/token",
                json={"habit_id": habit_id},
                headers={"X-Telegram-Init-Data": init_data},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert "token" in body and isinstance(body["token"], str)
        assert "expires_at" in body

        # Проверяем что токен валиден и содержит правильные claims.
        payload = pyjwt.decode(
            body["token"],
            "sse-test-secret",
            algorithms=["HS256"],
            audience="sse-stream",
        )
        assert payload["sub"] == str(user_id)
        assert payload["habit_id"] == habit_id
        assert payload["scope"] == "sse:today"
        assert payload["aud"] == "sse-stream"
        assert payload["iss"] == "backend"
        assert payload["exp"] > int(time.time())

    def test_non_member_gets_403(self, app: Any) -> None:
        """Юзер запрашивает токен для habit, в котором НЕ состоит → 403."""
        user_id = 99999  # нет membership
        habit_id = str(uuid.uuid4())  # несуществующий habit
        init_data = _build_init_data(user_id=user_id)

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/events/stream/token",
                json={"habit_id": habit_id},
                headers={"X-Telegram-Init-Data": init_data},
            )

        assert r.status_code == 403, r.text
        assert r.json()["code"] == "membership_not_active"

    def test_paused_member_gets_403(
        self, app: Any, habit_and_paused_member: tuple[str, int]
    ) -> None:
        """Paused member (status=paused) тоже не получает токен."""
        habit_id, user_id = habit_and_paused_member
        init_data = _build_init_data(user_id=user_id)
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/events/stream/token",
                json={"habit_id": habit_id},
                headers={"X-Telegram-Init-Data": init_data},
            )
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "membership_not_active"

    def test_missing_sse_token_secret_returns_503(
        self, app: Any, habit_and_member: tuple[str, int], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Если SSE_TOKEN_SECRET не задан в env → 503 (не 500), fail-closed."""
        habit_id, user_id = habit_and_member
        init_data = _build_init_data(user_id=user_id)

        monkeypatch.setenv("SSE_TOKEN_SECRET", "")
        get_settings.cache_clear()
        try:
            with TestClient(app) as client:
                r = client.post(
                    "/api/v1/events/stream/token",
                    json={"habit_id": habit_id},
                    headers={"X-Telegram-Init-Data": init_data},
                )
            assert r.status_code == 503, r.text
            assert r.json()["code"] == "sse_not_configured"
        finally:
            monkeypatch.setenv("SSE_TOKEN_SECRET", "sse-test-secret")
            get_settings.cache_clear()

    def test_missing_init_data_returns_401(self, app: Any) -> None:
        """Без X-Telegram-Init-Data → 401 missing_init_data (auth-уровень)."""
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/events/stream/token",
                json={"habit_id": "h"},
            )
        assert r.status_code == 401
        assert r.json()["code"] == "missing_init_data"

    def test_missing_habit_id_in_body_returns_422(
        self, app: Any, habit_and_member: tuple[str, int]
    ) -> None:
        """Без habit_id в теле → 422 (Pydantic validation)."""
        _, user_id = habit_and_member
        init_data = _build_init_data(user_id=user_id)
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/events/stream/token",
                json={},
                headers={"X-Telegram-Init-Data": init_data},
            )
        assert r.status_code == 422
