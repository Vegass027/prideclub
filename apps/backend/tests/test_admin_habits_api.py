"""Smoke-тесты admin API (TZ §3.6).

Используем SQLite + StaticPool (как в test_suspicious_pairs_service.py)
и паттерн подмены Postgres-функций из apps/worker/tests/conftest.py
(SQLCompiler.visit_gen_random_uuid/now/current_date + event listener
`_rewrite_sql_for_sqlite`).
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
from app.db import session as session_module
from app.main import create_app
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User


# --- Postgres → SQLite compatibility (паттерн из apps/worker/tests/conftest.py) ----


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
    """UUID/JSONB/INET → SQLite-совместимые типы (только для тестовых таблиц)."""
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


# --- helpers ---------------------------------------------------------------


def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
    """Валидный initData (TZ security.py:54 — WebAppData HMAC)."""
    user = {
        "id": user_id,
        "first_name": "Owner",
        "username": "owner",
    }
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "test_query",
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    hmac_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    params["hash"] = hmac_hash
    return urllib.parse.urlencode(params)


# --- fixtures --------------------------------------------------------------


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "12345")
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    # File-based SQLite + NullPool — изоляция между тестами (см. worker conftest).
    tmp_dir = tempfile.mkdtemp(prefix="hc_admin_test_")
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


def _owner_init_data() -> str:
    return _build_init_data(user_id=12345, bot_token="test-bot-token")


def _payload_chat(chat_id: int = -1001234567890) -> dict[str, Any]:
    return {
        "title": "Планка 30 мин",
        "description": "Держим планку каждый день",
        "photo_url": None,
        "telegram_invite_link": "https://t.me/+abc123",
        "stat_name": "Эстетика тела",
        "stat_icon": "💪",
        "chat_id": chat_id,
        "checkin_window_start": "06:00:00",
        "checkin_window_end": "11:00:00",
        "timezone": "Europe/Moscow",
        "proof_type": "video_note",
        "price_month": 100_00,
        "penalty_amount": 10_00,
        "stat_gain_per_checkin": 2,
        "stat_loss_per_miss": 1,
        "member_limit": None,
        "curator_id": None,
    }


# --- tests -----------------------------------------------------------------


class TestAdminHabitEndpoints:
    def test_create_habit_returns_201_with_is_active_false(
        self, app: Any
    ) -> None:
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["title"] == "Планка 30 мин"
        assert body["is_active"] is False
        assert body["archived_at"] is None
        assert body["id"]

    def test_create_habit_rejects_short_title(self, app: Any) -> None:
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=_payload_chat(chat_id=-1001234567891) | {"title": "ab"},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 422

    def test_create_habit_rejects_bad_timezone(self, app: Any) -> None:
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=_payload_chat(chat_id=-1001234567892) | {"timezone": "Mars/Olympus"},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 400
        assert r.json()["code"] == "habit_timezone_invalid"

    def test_list_habits_returns_created(self, app: Any) -> None:
        with TestClient(app) as client:
            client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
            r = client.get(
                "/admin/v1/habits",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "items" in body
        assert len(body["items"]) >= 1
        titles = {item["title"] for item in body["items"]}
        assert "Планка 30 мин" in titles

    def test_get_habit_by_id(self, app: Any) -> None:
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            r = client.get(
                f"/admin/v1/habits/{created['id']}",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200
        assert r.json()["id"] == created["id"]

    def test_get_unknown_habit_returns_404(self, app: Any) -> None:
        with TestClient(app) as client:
            r = client.get(
                "/admin/v1/habits/00000000-0000-0000-0000-000000000000",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 404
        assert r.json()["code"] == "habit_not_found"

    def test_patch_updates_only_provided_fields(self, app: Any) -> None:
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            r = client.patch(
                f"/admin/v1/habits/{created['id']}",
                json={"description": "Новое описание"},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["description"] == "Новое описание"
        assert body["title"] == "Планка 30 мин"

    def test_activate_sets_is_active_true(self, app: Any) -> None:
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            r = client.post(
                f"/admin/v1/habits/{created['id']}/activate",
                json={"is_active": True},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["is_active"] is True

    def test_archive_sets_archived_at_and_inactive(self, app: Any) -> None:
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            r = client.post(
                f"/admin/v1/habits/{created['id']}/archive",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["is_active"] is False
        assert body["archived_at"] is not None

    def test_activate_archived_returns_404(self, app: Any) -> None:
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            client.post(
                f"/admin/v1/habits/{created['id']}/archive",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
            r = client.post(
                f"/admin/v1/habits/{created['id']}/activate",
                json={"is_active": True},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 404
        assert r.json()["code"] == "habit_archived"

    def test_restore_clears_archived_keeps_inactive(self, app: Any) -> None:
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            client.post(
                f"/admin/v1/habits/{created['id']}/archive",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
            r = client.post(
                f"/admin/v1/habits/{created['id']}/restore",
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["archived_at"] is None
        assert body["is_active"] is False

    def test_non_owner_init_data_returns_403(self, app: Any) -> None:
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={
                    "X-Telegram-Init-Data": _build_init_data(
                        user_id=99999, bot_token="test-bot-token"
                    )
                },
            )
        assert r.status_code == 403
        assert r.json()["code"] == "not_owner"

    def test_missing_init_data_returns_401(self, app: Any) -> None:
        with TestClient(app) as client:
            r = client.post("/admin/v1/habits", json=_payload_chat())
        assert r.status_code == 401
        assert r.json()["code"] == "missing_init_data"
