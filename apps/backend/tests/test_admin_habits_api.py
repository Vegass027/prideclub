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
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db import session as session_module
from app.main import create_app
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.stat_definition import StatDefinition  # NEW (Phase 3 v2 Task 3.7)
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

    models = [User, Habit, Membership, StatDefinition]
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
    monkeypatch.setenv("BOT_TOKEN_ADMIN", "")
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
        await conn.run_sync(StatDefinition.__table__.create)
        # Phase 3 v2 Task 3.7: seed 1 активный stat_definition для happy-path.
        await conn.execute(
            StatDefinition.__table__.insert().values(
                id="11111111-1111-1111-1111-111111111111",
                slug="intelligence",
                name="Интеллект",
                icon="🧠",
                sort_order=1,
                is_active=True,
            )
        )

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


def _seed_stat_definitions(engine: Any) -> None:
    """No-op в текущей реализации (используется в фикстуре напрямую)."""
    pass


def _payload_chat(chat_id: int = -1001234567890) -> dict[str, Any]:
    """Default payload для POST /admin/v1/habits.

    Phase 3 v2 Task 3.7: stat_name/stat_icon УБРАНЫ, добавлен stat_definition_id.
    Дефолтное значение stat_definition_id совпадает с seeded UUID из
    _seed_stat_definitions(), чтобы happy-path tests работали out-of-box.
    """
    return {
        "title": "Планка 30 мин",
        "description": "Держим планку каждый день",
        "photo_url": None,
        "telegram_invite_link": "https://t.me/+abc123",
        "stat_definition_id": "11111111-1111-1111-1111-111111111111",
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
        # Phase 1 / migration 010: обязательны ссылки на топики чек-инов
        # и уведомлений (https://t.me/c/<chat_id>/<thread_id>).
        "checkin_topic_link": "https://t.me/c/-1001234567890/1",
        "notifications_topic_link": "https://t.me/c/-1001234567890/2",
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


# ── Phase 3 v2 Task 3.7: stat_definition_id contract ────────────


class TestAdminHabitStatDefinitionContract:
    """6 тестов для stat_definition_id FK contract (Task 3.7)."""

    async def test_create_habit_with_stat_definition_id_succeeds(
        self, app: Any, _sqlite_engine: Any,
    ) -> None:
        """POST с valid stat_definition_id UUID → 201; response содержит stat_definition_id."""
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 201, r.text
        body = r.json()
        # Phase 3 v2 Task 3.7: FK в response (free-text stat_name/stat_icon УБРАНЫ).
        assert body["stat_definition_id"] == "11111111-1111-1111-1111-111111111111"

    async def test_create_habit_with_missing_stat_definition_returns_400(
        self, app: Any,
    ) -> None:
        """POST с несуществующим stat_definition_id UUID → 400."""
        payload = _payload_chat()
        payload["stat_definition_id"] = "00000000-0000-0000-0000-000000000000"
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=payload,
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["code"] == "habit_stat_definition_not_found"

    async def test_create_habit_without_stat_definition_id_returns_422(
        self, app: Any,
    ) -> None:
        """POST без поля stat_definition_id → Pydantic 422."""
        payload = _payload_chat()
        del payload["stat_definition_id"]
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=payload,
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 422

    async def test_create_habit_with_inactive_stat_definition_returns_400(
        self, app: Any, _sqlite_engine: Any,
    ) -> None:
        """POST с is_active=false → 400 habit_stat_definition_inactive."""
        async with _sqlite_engine.begin() as conn:
            await conn.execute(
                StatDefinition.__table__.insert().values(
                    id="22222222-2222-2222-2222-222222222222",
                    slug="disabled",
                    name="Отключенная",
                    icon="⚡",
                    sort_order=99,
                    is_active=False,
                )
            )
        payload = _payload_chat()
        payload["stat_definition_id"] = "22222222-2222-2222-2222-222222222222"
        with TestClient(app) as client:
            r = client.post(
                "/admin/v1/habits",
                json=payload,
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["code"] == "habit_stat_definition_inactive"

    async def test_patch_habit_omitting_stat_definition_id_leaves_value_unchanged(
        self, app: Any,
    ) -> None:
        """PATCH без stat_definition_id → exclude_unset=True исключает ключ
        → service.update() не трогает колонку."""
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            # PATCH с другим полем, но stat_definition_id НЕ упомянут.
            r = client.patch(
                f"/admin/v1/habits/{created['id']}",
                json={"description": "Новое описание"},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # stat_definition_id остаётся прежним.
        assert body["stat_definition_id"] == "11111111-1111-1111-1111-111111111111"

    async def test_patch_habit_stat_definition_id_null_clears_value(
        self, app: Any,
    ) -> None:
        """PATCH {"stat_definition_id": null} → exclude_unset=True сохраняет ключ
        с None → service.update() ставит колонку в NULL."""
        with TestClient(app) as client:
            created = client.post(
                "/admin/v1/habits",
                json=_payload_chat(),
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            ).json()
            assert created["stat_definition_id"] is not None  # baseline
            # Явный null в payload — exclude_unset=True СОХРАНЯЕТ ключ с None.
            r = client.patch(
                f"/admin/v1/habits/{created['id']}",
                json={"stat_definition_id": None},
                headers={"X-Telegram-Init-Data": _owner_init_data()},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # stat_definition_id очищен в NULL.
        assert body["stat_definition_id"] is None
