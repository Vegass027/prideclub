"""AvatarService unit tests (Pravki.md §7.1 v3, подход D).

Покрывает:
    - get_or_fetch_local_path: cache hit (file + file_id match) → не вызывает Bot API.
    - get_or_fetch_local_path: cache miss → вызывает bot.getFile + скачивает,
      сохраняет на диск, кеширует file_id в Redis.
    - Bot API error / timeout → return None.
    - file_id=None → return None без обращения к Bot API.
    - get_cdn_url: legacy API (для обратной совместимости в worker).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_avatar_test_"))
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")

from app.core.config import get_settings  # noqa: E402
from app.services.avatar_service import AvatarService  # noqa: E402

# --- Fake infrastructure ---


class _FakeRedis:
    """Минимальный fake Redis: get/setex."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:  # noqa: ARG002
        self.store[key] = value


class _FakeResponse:
    """Минимальный fake aiohttp Response: json() + status."""

    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict[str, Any] | None = None,
        body: bytes = b"\xff\xd8\xff\xe0fake_jpeg",
    ) -> None:
        self.status = status
        self._payload = payload
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def json(self, content_type: str | None = None) -> dict[str, Any]:  # noqa: ARG002
        return self._payload or {}

    async def read(self) -> bytes:
        return self._body

    @property
    def content(self) -> _FakeContent:
        return _FakeContent(self._body)


class _FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, n: int = -1) -> bytes:  # noqa: ARG002
        return self._body


class _FakeHttp:
    """Минимальный fake aiohttp.ClientSession: get() возвращает _FakeResponse.

    Сценарии управляются через очередь responses — каждый get() берёт
    следующий response из очереди, последний повторяется.
    """

    def __init__(self, *responses: _FakeResponse) -> None:
        self._responses = list(responses) if responses else [_FakeResponse()]
        self.calls: list[dict[str, Any]] = []
        self.call_index = 0

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: Any | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.call_index < len(self._responses):
            resp = self._responses[self.call_index]
            self.call_index += 1
            return resp
        return self._responses[-1]


def _make_service(
    tmp_dir: Path,
    *,
    redis: _FakeRedis | None = None,
    http: _FakeHttp | None = None,
) -> AvatarService:
    return AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis or _FakeRedis(),  # type: ignore[arg-type]
        http=http or _FakeHttp(),  # type: ignore[arg-type]
        avatars_dir=tmp_dir / "avatars",
    )


# --- Tests ---


@pytest.mark.asyncio
async def test_avatar_returns_none_for_empty_file_id(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    result = await svc.get_or_fetch_local_path(123, None)
    assert result is None


@pytest.mark.asyncio
async def test_avatar_returns_cached_path_on_cache_hit(tmp_path: Path) -> None:
    """file_id совпадает с Redis + файл есть на диске → cache hit, без HTTP."""
    redis = _FakeRedis()
    redis.store["user_photo_file_id:123"] = "file_id_abc"

    # Заранее создаём файл.
    avatars_dir = tmp_path / "avatars"
    avatars_dir.mkdir()
    (avatars_dir / "123.jpg").write_bytes(b"cached_jpeg")

    http = _FakeHttp()  # НЕ должен вызываться.
    svc = _make_service(tmp_path, redis=redis, http=http)

    result = await svc.get_or_fetch_local_path(123, "file_id_abc")
    assert result == avatars_dir / "123.jpg"
    assert result.exists()
    assert result.read_bytes() == b"cached_jpeg"
    assert http.calls == []  # НЕ обращались к Bot API.


@pytest.mark.asyncio
async def test_avatar_downloads_on_cache_miss(tmp_path: Path) -> None:
    """file_id новый → скачиваем с Telegram, сохраняем на диск, кешируем."""
    redis = _FakeRedis()
    # getFile → {"ok": true, "result": {"file_path": "photos/fresh.jpg"}}
    getfile_resp = _FakeResponse(
        payload={"ok": True, "result": {"file_path": "photos/fresh.jpg"}}
    )
    # GET cdn_url → JPEG bytes
    cdn_resp = _FakeResponse(body=b"\xff\xd8\xff\xe0fresh_jpeg_bytes")
    http = _FakeHttp(getfile_resp, cdn_resp)
    svc = _make_service(tmp_path, redis=redis, http=http)

    result = await svc.get_or_fetch_local_path(123, "file_id_abc")
    assert result is not None
    assert result.exists()
    assert result.read_bytes() == b"\xff\xd8\xff\xe0fresh_jpeg_bytes"
    # Два HTTP-запроса: getFile + GET CDN.
    assert len(http.calls) == 2
    assert "getFile" in http.calls[0]["url"]
    assert "file/bot" in http.calls[1]["url"]
    # Redis закэшировал file_id.
    assert redis.store["user_photo_file_id:123"] == "file_id_abc"


@pytest.mark.asyncio
async def test_avatar_re_downloads_when_file_id_changes(tmp_path: Path) -> None:
    """file_id в БД отличается от Redis → cache miss, скачиваем заново."""
    redis = _FakeRedis()
    redis.store["user_photo_file_id:123"] = "old_file_id"

    getfile_resp = _FakeResponse(
        payload={"ok": True, "result": {"file_path": "photos/new.jpg"}}
    )
    cdn_resp = _FakeResponse(body=b"new_jpeg")
    http = _FakeHttp(getfile_resp, cdn_resp)
    svc = _make_service(tmp_path, redis=redis, http=http)

    result = await svc.get_or_fetch_local_path(123, "new_file_id")
    assert result is not None
    assert result.read_bytes() == b"new_jpeg"
    # file_id обновлён в Redis.
    assert redis.store["user_photo_file_id:123"] == "new_file_id"


@pytest.mark.asyncio
async def test_avatar_returns_none_when_bot_api_fails(tmp_path: Path) -> None:
    """getFile вернул {"ok": false} → return None, не пишем на диск."""
    redis = _FakeRedis()
    http = _FakeHttp(_FakeResponse(payload={"ok": False, "description": "file not found"}))
    svc = _make_service(tmp_path, redis=redis, http=http)

    result = await svc.get_or_fetch_local_path(123, "file_id_abc")
    assert result is None
    # Файл не создался.
    assert not (tmp_path / "avatars" / "123.jpg").exists()
    # file_id в Redis не закеширован.
    assert "user_photo_file_id:123" not in redis.store


@pytest.mark.asyncio
async def test_avatar_returns_none_on_network_error(tmp_path: Path) -> None:
    """getFile выбросил aiohttp.ClientError → return None."""
    import aiohttp

    redis = _FakeRedis()

    class _FailingHttp:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            class _R:
                async def __aenter__(self) -> _R:
                    raise aiohttp.ClientError("connection refused")

                async def __aexit__(self, *exc: Any) -> None:
                    pass

            return _R()

    svc = AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,  # type: ignore[arg-type]
        http=_FailingHttp(),  # type: ignore[arg-type]
        avatars_dir=tmp_path / "avatars",
    )
    result = await svc.get_or_fetch_local_path(123, "file_id_abc")
    assert result is None


@pytest.mark.asyncio
async def test_avatar_returns_none_on_cdn_download_non_200(tmp_path: Path) -> None:
    """getFile OK, но GET CDN вернул 404 → return None."""
    redis = _FakeRedis()
    getfile_resp = _FakeResponse(
        payload={"ok": True, "result": {"file_path": "photos/x.jpg"}}
    )
    cdn_resp = _FakeResponse(status=404, payload={"ok": False})
    http = _FakeHttp(getfile_resp, cdn_resp)
    svc = _make_service(tmp_path, redis=redis, http=http)

    result = await svc.get_or_fetch_local_path(123, "file_id_abc")
    assert result is None
    assert not (tmp_path / "avatars" / "123.jpg").exists()


@pytest.mark.asyncio
async def test_avatar_returns_none_on_oversized_response(tmp_path: Path) -> None:
    """CDN вернул файл больше MAX_JPEG_BYTES → return None."""
    redis = _FakeRedis()
    getfile_resp = _FakeResponse(
        payload={"ok": True, "result": {"file_path": "photos/huge.jpg"}}
    )
    # Создаём response с content.read() который вернёт > 5MB
    big_body = b"x" * (5 * 1024 * 1024 + 1)

    class _BigResponse:
        status = 200

        async def __aenter__(self) -> _BigResponse:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            pass

        @property
        def content(self) -> Any:
            class _C:
                def __init__(self, body: bytes) -> None:
                    self._body = body

                async def read(self, n: int = -1) -> bytes:  # noqa: ARG002
                    return self._body

            return _C(big_body)

    class _MixedHttp:
        def __init__(self, first: Any, second: Any) -> None:
            self._first = first
            self._second = second
            self.idx = 0

        def get(self, *args: Any, **kwargs: Any) -> Any:
            self.idx += 1
            return self._first if self.idx == 1 else self._second

    http = _MixedHttp(getfile_resp, _BigResponse())
    svc = AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,  # type: ignore[arg-type]
        http=http,  # type: ignore[arg-type]
        avatars_dir=tmp_path / "avatars",
    )
    result = await svc.get_or_fetch_local_path(123, "file_id_abc")
    assert result is None


@pytest.mark.asyncio
async def test_avatar_legacy_get_cdn_url_works(tmp_path: Path) -> None:
    """get_cdn_url — legacy API для worker fallback. Не ломаем."""
    redis = _FakeRedis()
    http = _FakeHttp(_FakeResponse(payload={"ok": True, "result": {"file_path": "photos/x.jpg"}}))
    svc = _make_service(tmp_path, redis=redis, http=http)

    result = await svc.get_cdn_url(123, "file_id_abc")
    assert result is not None
    assert result.endswith("/photos/x.jpg")
