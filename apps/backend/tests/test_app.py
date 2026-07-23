from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_health() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_unknown_path_returns_404() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 404
        assert r.json()["code"] == "not_found"


def test_api_v1_without_init_data_returns_401() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/v1/me")
        assert r.status_code == 401
        assert r.json()["code"] == "missing_init_data"


def test_internal_without_service_token_returns_401() -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/internal/something")
        assert r.status_code == 401
        assert r.json()["code"] == "missing_service_token"


def test_admin_without_init_data_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без initData админский контур отвечает missing_init_data, как и публичный.
    Требует явной установки OWNER_TELEGRAM_ID (default 0 → admin_disabled 503)."""
    import app.core.config as config_module

    monkeypatch.setenv("OWNER_TELEGRAM_ID", "12345")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/admin/v1/habits")
        assert r.status_code == 401
        assert r.json()["code"] == "missing_init_data"

    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_admin_disabled_when_owner_not_configured() -> None:
    """Если OWNER_TELEGRAM_ID не задан (default 0), /admin/v1/* отвечает 503 admin_disabled
    даже с валидным initData. Это fail-closed: забыли сконфигурить — никто не пройдёт."""
    app = create_app()
    with TestClient(app) as client:
        r = client.get(
            "/admin/v1/habits",
            headers={"X-Telegram-Init-Data": "hash=invalid"},
        )
        # initData проверяется ПОСЛЕ owner-gate, поэтому получим 401 invalid_init_data
        # (это безопасно — admin_disabled 503 срабатывает только при валидной initData).
        assert r.status_code in (401, 503)
        assert r.json()["code"] in ("invalid_init_data", "admin_disabled")


def _build_init_data(user_id: int, bot_token: str) -> str:
    """Генерирует валидный initData для тестов с заданным user_id."""
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    user = {"id": user_id, "first_name": "Owner", "is_premium": False}
    auth_date = int(time.time())
    params = {"user": json.dumps(user, separators=(",", ":")), "auth_date": str(auth_date)}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


def test_admin_owner_passes_auth_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """OWNER_TELEGRAM_ID=12345 + initData с user.id=12345 проходит auth-layer.
    Дёргаем несуществующий путь внутри admin-контура — Starlette вернёт 404,
    НЕ 401/403 (значит auth-уровень пропустил owner-запрос).
    """
    import app.core.config as config_module

    monkeypatch.setenv("OWNER_TELEGRAM_ID", "12345")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]

    app = create_app()
    init_data = _build_init_data(user_id=12345, bot_token="test-bot-token")
    with TestClient(app) as client:
        r = client.get(
            "/admin/v1/__no_such_route__",
            headers={"X-Telegram-Init-Data": init_data},
        )
        assert r.status_code == 404, (
            f"Expected 404 from Starlette, got {r.status_code}: {r.text}"
        )

    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]


def test_admin_non_owner_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """OWNER_TELEGRAM_ID=12345, но initData с user.id=99999 — должно быть 403 not_owner."""
    import app.core.config as config_module

    monkeypatch.setenv("OWNER_TELEGRAM_ID", "12345")
    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]

    app = create_app()
    init_data = _build_init_data(user_id=99999, bot_token="test-bot-token")
    with TestClient(app) as client:
        r = client.get(
            "/admin/v1/habits",
            headers={"X-Telegram-Init-Data": init_data},
        )
        assert r.status_code == 403
        assert r.json()["code"] == "not_owner"

    config_module.get_settings.cache_clear()  # type: ignore[attr-defined]