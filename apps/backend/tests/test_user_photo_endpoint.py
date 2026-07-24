"""Integration tests for GET /api/v1/users/{id}/photo (Pravki.md §7.1).

Подход C': endpoint делает 307 redirect на Telegram CDN с токеном бота в
Location header. Токен НЕ попадает в JSON клиента (Location header — server-side).

Безопасность:
    - TelegramUserDep требует initData (401 без него).
    - 404 если photo_file_id отсутствует.
    - 502 если Bot API недоступен.

Mock'и:
    - SQLite-таблица users с photo_file_id.
    - AvatarService.get_cdn_url подменяется через monkeypatch (без реального HTTP).
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
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.compiler import SQLCompiler

# --- env vars ДО импорта create_app ---
# STATIC_DIR обязателен до from app.main import create_app (там main.py:115
# делает os.makedirs(_static_dir) на module-level).
os.environ["STATIC_DIR"] = tempfile.mkdtemp(prefix="hc_photo_test_static_")
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")

from app.core.config import get_settings  # noqa: E402
from app.core.deps import get_avatar_service  # noqa: E402
from app.db import session as session_module  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.habit import Habit  # noqa: E402
from app.models.membership import Membership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.avatar_service import AvatarService  # noqa: E402

# --- Postgres → SQLite shims ---


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return f"'{uuid.uuid4()}'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq):
    import re

    def _repl_uuid(_m):
        _uuid_seq[0] += 1
        return f"'{uuid.uuid4()}'"

    statement = re.sub(r"gen_random_uuid\s*\(\s*\)", _repl_uuid, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bnow\s*\(\s*\)", "CURRENT_TIMESTAMP", statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bcurrent_date\b", "CURRENT_DATE", statement, flags=re.IGNORECASE)
    return statement, parameters


def _remap_postgres_types_for_sqlite() -> None:
    from sqlalchemy import JSON, String
    from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

    for m in [User, Habit, Membership]:
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)


_remap_postgres_types_for_sqlite()


# --- helpers ---


def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
    user = {"id": user_id, "first_name": "User", "username": "u"}
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "q",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


# --- fixtures ---


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("STATIC_DIR", tempfile.mkdtemp(prefix="hc_photo_test_static_"))
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_photo_test_")
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

    # Таблица users (упрощённо — нужны только нужные колонки)
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)

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
def app_with_mocked_avatar(_sqlite_engine: Any):
    """Создаёт app с подменённым AvatarService (без реального HTTP)."""
    engine, _ = _sqlite_engine
    app = create_app()

    # Подменяем AvatarService на mock — get_cdn_url возвращает фиксированный URL
    fake_svc = AsyncMock(spec=AvatarService)
    fake_svc.get_cdn_url.return_value = (
        "https://api.telegram.org/file/botTEST/photos/file_42.jpg"
    )
    app.dependency_overrides[get_avatar_service] = lambda: fake_svc
    return app, fake_svc


async def _seed_user_with_photo(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    photo_file_id: str | None,
) -> None:
    async with factory() as s:
        s.add(
            User(
                id=user_id,
                first_name="Test",
                username="t",
                photo_file_id=photo_file_id,
            )
        )
        await s.commit()


# --- tests ---


def test_photo_endpoint_requires_init_data(app_with_mocked_avatar: tuple[Any, Any]) -> None:
    app, _ = app_with_mocked_avatar
    with TestClient(app) as client:
        r = client.get("/api/v1/users/123/photo")
    assert r.status_code == 401
    assert r.json()["code"] == "missing_init_data"


def test_photo_endpoint_404_when_no_photo_file_id(
    app_with_mocked_avatar: tuple[Any, Any], _sqlite_engine: Any
) -> None:
    app, _ = app_with_mocked_avatar
    _, factory = _sqlite_engine
    import asyncio

    asyncio.run(_seed_user_with_photo(factory, user_id=456, photo_file_id=None))

    with TestClient(app) as client:
        r = client.get(
            "/api/v1/users/456/photo",
            headers={"X-Telegram-Init-Data": _build_init_data(user_id=456)},
        )
    assert r.status_code == 404
    assert r.json()["code"] == "photo_unavailable"


def test_photo_endpoint_returns_jpeg_with_photo(
    app_with_mocked_avatar: tuple[Any, Any], _sqlite_engine: Any
) -> None:
    """Pravki.md §7.1 v3 (подход D): endpoint отдаёт JPEG из локального кеша,
    не 307 redirect. Токен бота НЕ утекает в Network tab.
    """
    import tempfile
    from pathlib import Path

    from app.core.deps import get_avatar_service

    app, fake_svc = app_with_mocked_avatar
    _, factory = _sqlite_engine
    import asyncio

    asyncio.run(
        _seed_user_with_photo(factory, user_id=789, photo_file_id="AgAC_file_id_xyz")
    )

    # Override DI чтобы AvatarService.get_or_fetch_local_path вернул
    # реальный JPEG-файл (а не замоканный Path).
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(b"\xff\xd8\xff\xe0fake_jpeg_bytes")
        tmp_path = Path(tmp.name)

    real_svc = AsyncMock(spec=AvatarService)
    real_svc.get_or_fetch_local_path.return_value = tmp_path

    async def _override():
        return real_svc

    app.dependency_overrides[get_avatar_service] = _override

    with TestClient(app) as client:
        r = client.get(
            "/api/v1/users/789/photo",
            headers={"X-Telegram-Init-Data": _build_init_data(user_id=789)},
        )
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, max-age=21600"
    # Бинарь = содержимое tmp-файла.
    assert r.content == b"\xff\xd8\xff\xe0fake_jpeg_bytes"
    # AvatarService был вызван с правильным user_id и file_id.
    real_svc.get_or_fetch_local_path.assert_awaited_once()
    call_args = real_svc.get_or_fetch_local_path.call_args
    assert call_args.args[0] == 789
    assert call_args.args[1] == "AgAC_file_id_xyz"

    tmp_path.unlink()


def test_photo_endpoint_404_when_avatar_service_returns_none(
    app_with_mocked_avatar: tuple[Any, Any], _sqlite_engine: Any
) -> None:
    """Если AvatarService.get_or_fetch_local_path возвращает None
    (Telegram недоступен / file_id есть но скачать нельзя) → 404."""
    from app.core.deps import get_avatar_service

    app, _ = app_with_mocked_avatar
    _, factory = _sqlite_engine
    import asyncio

    asyncio.run(
        _seed_user_with_photo(factory, user_id=890, photo_file_id="AgAC_some_file")
    )

    real_svc = AsyncMock(spec=AvatarService)
    real_svc.get_or_fetch_local_path.return_value = None

    async def _override():
        return real_svc

    app.dependency_overrides[get_avatar_service] = _override

    with TestClient(app) as client:
        r = client.get(
            "/api/v1/users/890/photo",
            headers={"X-Telegram-Init-Data": _build_init_data(user_id=890)},
        )
    assert r.status_code == 404
    assert r.json()["code"] == "photo_unavailable"
