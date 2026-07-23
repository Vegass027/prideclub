"""Тест фабрики service-токенов в main.py.

Регрессия: фабрика кэшировала токен через @lru_cache(maxsize=1),
и через 60 сек токен протухал, а cache возвращал старый → backend
отвечал 401 Unauthorized. Тест проверяет, что фабрика выдаёт
новый токен на каждый вызов (или, как минимум, что время iat
монотонно растёт между вызовами).
"""
from __future__ import annotations

import time

import jwt

from bot.config import Settings
from bot.main import _make_backend_token_factory


def _fake_settings() -> Settings:
    """Минимальный Settings для генератора токена."""
    return Settings(
        bot_token="x",
        service_secret="test-secret-32bytes-1234567890abcde",
        webhook_base_url="",
        webhook_path="/bot/webhook",
        webhook_secret="x",
        backend_url="http://localhost:8000",
        environment="development",
    )


def test_factory_returns_fresh_token_each_call() -> None:
    factory = _make_backend_token_factory(_fake_settings())

    t1 = factory()
    # Чтобы iat точно отличался — задержка > 1 сек.
    time.sleep(1.1)
    t2 = factory()

    decoded1 = jwt.decode(
        t1,
        "test-secret-32bytes-1234567890abcde",
        algorithms=["HS256"],
        audience="backend-api",
    )
    decoded2 = jwt.decode(
        t2,
        "test-secret-32bytes-1234567890abcde",
        algorithms=["HS256"],
        audience="backend-api",
    )

    assert decoded1["iat"] < decoded2["iat"], (
        "Factory must NOT cache: iat must grow between calls. "
        "If this fails — @lru_cache вернулся, и через TTL=60s backend даст 401."
    )


def test_factory_token_has_correct_claims() -> None:
    factory = _make_backend_token_factory(_fake_settings())
    token = factory()
    decoded = jwt.decode(
        token,
        "test-secret-32bytes-1234567890abcde",
        algorithms=["HS256"],
        audience="backend-api",
    )

    assert decoded["iss"] == "bot"
    assert decoded["service"] == "bot"
    assert decoded["aud"] == "backend-api"
    assert "exp" in decoded
    assert "iat" in decoded
    # TTL 60 сек.
    assert decoded["exp"] - decoded["iat"] == 60