"""Tests для GET /admin/v1/stat-definitions (Phase 3 v2 Task 3.7)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse

# env bootstrap (как в test_admin_habits_api.py).
os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_sd_test_"))
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SSE_TOKEN_SECRET", "test-sse-token-secret")
os.environ.setdefault("OWNER_TELEGRAM_ID", "12345")

# Force-clear cached settings BEFORE imports trigger get_settings().
# В settings.bot_token сохранён старый production env var из pre-test
# session state. cache_clear() at fixture body runs AFTER some
# imports may have already populated the cache.
from app.core.config import get_settings
get_settings.cache_clear()

import uuid as _uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import JSON, String, event as _sa_event  # noqa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID  # noqa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.compiler import SQLCompiler

from app.core.config import get_settings
from app.db import session as session_module
from app.main import create_app
from app.models.stat_definition import StatDefinition
from app.models.user import User

# Postgres → SQLite shims.
SQLCompiler.visit_gen_random_uuid = lambda *a, **k: f"'{_uuid.uuid4()}'"  # type: ignore
SQLCompiler.visit_now = lambda *a, **k: "CURRENT_TIMESTAMP"  # type: ignore

for _m in [User, StatDefinition]:
    for _col in _m.__table__.columns:
        _t = _col.type
        if isinstance(_t, UUID) and not _t.as_uuid:
            _col.type = String(36)
        elif isinstance(_t, JSONB):
            _col.type = JSON()
        elif isinstance(_t, INET):
            _col.type = String(45)


# initData + owner (как в test_admin_habits_api.py).
def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
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


def _owner_headers() -> dict[str, str]:
    """InitData для owner'а (user_id=12345 == OWNER_TELEGRAM_ID)."""
    return {"X-Telegram-Init-Data": _build_init_data(user_id=12345)}


def _non_owner_headers() -> dict[str, str]:
    """InitData для НЕ-owner (user_id=1)."""
    return {"X-Telegram-Init-Data": _build_init_data(user_id=1)}


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "12345")
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("BOT_TOKEN_ADMIN", "")  # Force fallback to BOT_TOKEN
    monkeypatch.setenv("SERVICE_SECRET", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_sd_test_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(StatDefinition.__table__.create)

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


# ── tests ────────────────────────────────────────────────────


async def test_stat_definitions_endpoint_returns_8_active_in_sort_order(
    _sqlite_engine: Any, app: Any,
) -> None:
    """GET → 200, items.length=8, ORDER BY sort_order ASC."""
    canonical = [
        ("intelligence", "Интеллект", "🧠", 1),
        ("strength", "Сила", "💪", 2),
        ("endurance", "Выносливость", "🫁", 3),
        ("balance", "Баланс", "🧘", 4),
        ("energy", "Энергия", "✨", 5),
        ("focus", "Фокус", "🎯", 6),
        ("creativity", "Творчество", "🎨", 7),
        ("connections", "Связи", "🤝", 8),
    ]
    async with _sqlite_engine.begin() as conn:
        for slug, name, icon, sort_order in canonical:
            await conn.execute(
                StatDefinition.__table__.insert().values(
                    id=f"sd-{slug}",
                    slug=slug,
                    name=name,
                    icon=icon,
                    sort_order=sort_order,
                    is_active=True,
                )
            )

    with TestClient(app) as client:
        resp = client.get("/admin/v1/stat-definitions", headers=_owner_headers())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 8
    assert len(body["items"]) == 8

    # ORDER BY sort_order ASC.
    sort_orders = [item["sort_order"] for item in body["items"]]
    assert sort_orders == sorted(sort_orders)
    assert sort_orders == [1, 2, 3, 4, 5, 6, 7, 8]

    # First item = intelligence (sort_order=1).
    assert body["items"][0]["slug"] == "intelligence"
    assert body["items"][0]["icon"] == "🧠"


async def test_stat_definitions_endpoint_is_owner_only(
    _sqlite_engine: Any, app: Any,
) -> None:
    """Owner authorization contract:
    - нет initData → 401 missing_init_data;
    - initData с user_id=1 (≠ owner) → 403 not_owner;
    - initData с user_id=12345 (== owner) → 200.
    """
    async with _sqlite_engine.begin() as conn:
        await conn.execute(
            StatDefinition.__table__.insert().values(
                id="sd-intel",
                slug="intelligence",
                name="Интеллект",
                icon="🧠",
                sort_order=1,
                is_active=True,
            )
        )

    with TestClient(app) as client:
        # 1. Без X-Telegram-Init-Data → 401.
        resp = client.get("/admin/v1/stat-definitions")
        assert resp.status_code == 401, resp.text
        assert resp.json()["code"] == "missing_init_data"

        # 2. InitData для НЕ-owner (user_id=1) → 403.
        resp = client.get("/admin/v1/stat-definitions", headers=_non_owner_headers())
        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "not_owner"

        # 3. InitData для owner (user_id=12345) → 200.
        resp = client.get("/admin/v1/stat-definitions", headers=_owner_headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["slug"] == "intelligence"


async def test_stat_definitions_endpoint_returns_expected_schema_fields(
    _sqlite_engine: Any, app: Any,
) -> None:
    """Каждый item: id (UUID str), slug, name, icon, sort_order."""
    async with _sqlite_engine.begin() as conn:
        await conn.execute(
            StatDefinition.__table__.insert().values(
                id="sd-intel",
                slug="intelligence",
                name="Интеллект",
                icon="🧠",
                sort_order=1,
                is_active=True,
            )
        )

    with TestClient(app) as client:
        resp = client.get("/admin/v1/stat-definitions", headers=_owner_headers())

    assert resp.status_code == 200
    body = resp.json()
    item = body["items"][0]
    # Schema contract — точно эти поля.
    assert set(item.keys()) == {"id", "slug", "name", "icon", "sort_order"}
    assert item["id"] == "sd-intel"
    assert item["slug"] == "intelligence"
    assert item["name"] == "Интеллект"
    assert item["icon"] == "🧠"
    assert item["sort_order"] == 1
