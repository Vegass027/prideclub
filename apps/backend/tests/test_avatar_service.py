"""AvatarService unit tests (Pravki.md §7.1).

Покрывает:
    - Redis cache hit → не вызывает Bot API.
    - Cache miss → вызывает bot.getFile, кэширует, возвращает CDN URL.
    - Bot API error / timeout → return None (Avatar fallback на инициалы).
    - file_id=None → return None без обращения к Bot API.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

# STATIC_DIR должен быть выставлен ДО импорта create_app (см. test_topup.py).
os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_avatar_test_"))
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")

from app.core.config import get_settings  # noqa: E402
from app.services.avatar_service import AvatarService  # noqa: E402


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self.store[key] = value


class _FakeResponse:
    def __init__(self, *, ok: bool, payload: dict[str, Any] | None = None) -> None:
        self._ok = ok
        self._payload = payload or {}

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def json(self, content_type: str | None = None) -> dict[str, Any]:  # noqa: ARG002
        return {"ok": self._ok, "result": self._payload}


class _FakeHttp:
    def __init__(self, *, ok: bool, payload: dict[str, Any] | None = None) -> None:
        self._response = _FakeResponse(ok=ok, payload=payload)
        self.calls = 0

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: Any | None = None,
    ) -> _FakeResponse:
        self.calls += 1
        return self._response


@pytest.mark.asyncio
async def test_avatar_returns_none_for_empty_file_id() -> None:
    redis = _FakeRedis()
    http = _FakeHttp(ok=True, payload={"file_path": "photos/x.jpg"})
    svc = AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,  # type: ignore[arg-type]
        http=http,  # type: ignore[arg-type]
    )
    result = await svc.get_cdn_url(123, None)
    assert result is None
    assert http.calls == 0
    assert redis.store == {}


@pytest.mark.asyncio
async def test_avatar_uses_redis_cache_without_calling_bot() -> None:
    redis = _FakeRedis()
    redis.store["user_photo:123"] = "photos/cached.jpg"
    http = _FakeHttp(ok=True, payload={"file_path": "photos/fresh.jpg"})
    svc = AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,  # type: ignore[arg-type]
        http=http,  # type: ignore[arg-type]
    )
    result = await svc.get_cdn_url(123, "file_id_abc")
    assert result is not None
    assert result.endswith("/photos/cached.jpg")
    assert get_settings().bot_token in result
    assert http.calls == 0


@pytest.mark.asyncio
async def test_avatar_fetches_from_bot_on_cache_miss() -> None:
    redis = _FakeRedis()
    http = _FakeHttp(ok=True, payload={"file_path": "photos/fresh.jpg"})
    svc = AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,  # type: ignore[arg-type]
        http=http,  # type: ignore[arg-type]
    )
    result = await svc.get_cdn_url(123, "file_id_abc")
    assert result is not None
    assert result.endswith("/photos/fresh.jpg")
    assert http.calls == 1
    # Должен был закэшироваться
    assert redis.store["user_photo:123"] == "photos/fresh.jpg"


@pytest.mark.asyncio
async def test_avatar_returns_none_when_bot_api_fails() -> None:
    redis = _FakeRedis()
    http = _FakeHttp(ok=False, payload={"description": "file not found"})
    svc = AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,  # type: ignore[arg-type]
        http=http,  # type: ignore[arg-type]
    )
    result = await svc.get_cdn_url(123, "file_id_abc")
    assert result is None
    assert http.calls == 1
    # Не должно кэшироваться ошибка
    assert "user_photo:123" not in redis.store
