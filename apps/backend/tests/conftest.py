from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# ВАЖНО: STATIC_DIR нужно выставить ДО импорта app.main, потому что
# на module-level в main.py вызывается create_app(), который делает
# os.makedirs(STATIC_DIR). На macOS /app недоступен для записи.
_TMP_STATIC = tempfile.mkdtemp(prefix="hc_static_")
os.environ.setdefault("STATIC_DIR", _TMP_STATIC)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "packages" / "shared"))
sys.path.insert(0, str(ROOT / "apps" / "backend"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    # STATIC_DIR уже выставлен на module-level (см. выше), но явно дублируем
    # через fixture, чтобы monkeypatch не откатывал его на дефолт "/app/static"
    # в случае если другие тесты меняют переменные.
    monkeypatch.setenv("STATIC_DIR", _TMP_STATIC)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test-service-secret")
    monkeypatch.setenv("SSE_TOKEN_SECRET", "test-sse-token-secret")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"