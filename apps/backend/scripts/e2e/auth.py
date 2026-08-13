"""Генерация валидных credentials для e2e-сценариев.

Это инверсия `packages/shared/security.py:validate_init_data` —
HMAC-SHA256 с secret="WebAppData"+bot_token, data_check_string с
sort by key, raw values (без двойного percent-encoding).

Пример:
    user = FakeUser(id=99001, first_name="E2E", username="e2e_1")
    headers = {"X-Telegram-Init-Data": generate_init_data(user, bot_token=...)}

Алгоритм MUST stay 1-в-1 с validate_init_data (apps/backend/app/core/security.py).
Любой drift = 401 invalid_init_data. Сверять при изменениях.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass

import jwt


@dataclass(frozen=True, slots=True)
class FakeUser:
    """Синтетический Telegram-пользователь для e2e.

    user_id берём из диапазона 99xxx (99001-99099) — чтобы не конфликтовать
    с seed-юзерами в БД (alice..eve = 10001-10005).
    """

    id: int
    first_name: str
    username: str | None = None
    last_name: str | None = None
    language_code: str | None = "ru"
    is_premium: bool = False

    def to_json(self) -> str:
        """Сериализация в Telegram user object (URL-decoded JSON).

        Ключи и значения — канонические (id=число, is_premium=bool).
        """
        payload: dict[str, object] = {
            "id": self.id,
            "first_name": self.first_name,
            "is_premium": self.is_premium,
        }
        if self.username is not None:
            payload["username"] = self.username
        if self.last_name is not None:
            payload["last_name"] = self.last_name
        if self.language_code is not None:
            payload["language_code"] = self.language_code
        return json.dumps(payload, separators=(",", ":"))


def generate_init_data(
    user: FakeUser,
    *,
    bot_token: str,
    auth_date: int | None = None,
    query_id: str = "e2e",
) -> str:
    """Возвращает валидный query string для X-Telegram-Init-Data.

    Контракт (1-в-1 с packages/shared/security.py:validate_init_data):
        1. data_check_string = "\\n".join(sorted(k=v))
        2. secret_key = hmac(b"WebAppData", bot_token)
        3. hash = hmac(secret_key, data_check_string).hexdigest()
        4. urlencode (user field = URL-encoded JSON, hash в конце)
    """
    params: dict[str, str] = {
        "user": user.to_json(),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": query_id,
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check.encode(), hashlib.sha256
    ).hexdigest()
    # urlencode percent-кодирует user JSON (валидный input для parse_qsl на
    # стороне validate_init_data, где parse_qsl раскодирует обратно).
    params["hash"] = computed_hash
    return urllib.parse.urlencode(params)


def generate_service_token(
    *,
    service_name: str,
    target_audience: str,
    secret: str,
    ttl_seconds: int = 60,
) -> str:
    """JWT HS256 для X-Service-Token (bot/worker → /internal/*).

    Копия `packages/shared/security.py:generate_service_token` —
    здесь продублирована, чтобы e2e-сценарий не зависел от
    packages/shared (который на проде копируется в каждый Python-образ).
    """
    now = int(time.time())
    payload = {
        "service": service_name,
        "iss": service_name,
        "aud": target_audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")
