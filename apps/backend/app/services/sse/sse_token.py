"""SSE-specific short-lived JWT tokens.

Отдельный секрет (SSE_TOKEN_SECRET) от SERVICE_SECRET — разные контуры,
разная blast-radius при компрометации (см. AGENTS.md и sse+redis.md §3.12):

- SERVICE_SECRET — internal-контур (/internal/*), между trusted сервисами.
- SSE_TOKEN_SECRET — пользовательский контур (/api/v1/events/*), токен
  выдаётся по initData юзера.

Audience "sse-stream" отличается от "backend-api" (service-tokens).
Scope "sse:today" — единственная допустимая операция для токена,
защита от ошибочного использования SSE-токена как service-token'а.

TTL 60с — за это время клиент обязан открыть EventSource. Ручной
reconnect-loop (см. useTodayStream.ts) получает свежий токен на каждое
открытие — нативный EventSource auto-reconnect не используется, иначе
реконнект возьмёт тот же URL с протухшим токеном → 401 → permanent
close per spec (см. sse+redis.md §3.13).
"""
from __future__ import annotations

import time
from typing import Any

import jwt

from app.core.exceptions import (
    InvalidServiceTokenError,
    ServiceTokenExpiredError,
)

SSE_TOKEN_AUDIENCE = "sse-stream"  # noqa: S105 — public JWT aud, не секрет
SSE_TOKEN_SCOPE = "sse:today"  # noqa: S105 — public JWT scope, не секрет
SSE_TOKEN_ISSUER = "backend"  # noqa: S105 — public JWT iss, не секрет
SSE_TOKEN_DEFAULT_TTL_SECONDS = 60
# Leeway при валидации exp/iat — устойчивость к дрейфу часов между
# контейнерами backend/worker. 10с — компромисс: достаточно для типичного
# NTP-дрейфа, но не настолько много, чтобы "продлевать" явно протухшие
# токены. Защищает от ложных 401 на границе exp при частом reconnect
# (useTodayStream.ts открывает новый EventSource на каждое событие onerror).
SSE_TOKEN_LEEWAY_SECONDS = 10


def generate_sse_token(
    *,
    user_id: int,
    habit_id: str,
    secret: str,
    ttl_seconds: int = SSE_TOKEN_DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> tuple[str, int]:
    """Подписать SSE-токен для (user_id, habit_id). Возвращает (token, exp_unix).

    Raises:
        ValueError: secret пуст (мисконфиг сервера).
    """
    if not secret:
        raise ValueError("SSE_TOKEN_SECRET is empty")
    iat = int(time.time()) if now is None else now
    exp = iat + ttl_seconds
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "habit_id": habit_id,
        "scope": SSE_TOKEN_SCOPE,
        "aud": SSE_TOKEN_AUDIENCE,
        "iss": SSE_TOKEN_ISSUER,
        "iat": iat,
        "exp": exp,
    }
    return jwt.encode(payload, secret, algorithm="HS256"), exp


def validate_sse_token(
    token: str,
    *,
    secret: str,
    expected_habit_id: str,
) -> dict[str, Any]:
    """Валидировать SSE-токен и проверить, что habit_id в claim совпадает.

    Raises:
        InvalidServiceTokenError — битая подпись / aud/scope не совпали /
            habit_id в query не совпал с claim / нет обязательных claims.
        ServiceTokenExpiredError — exp истёк.

    (Переиспользуем существующие service-token-исключения: status_code=401
    и code унифицированы с /internal/* ошибками, фронту не нужно различать
    эти два класса ошибок авторизации.)
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=SSE_TOKEN_AUDIENCE,
            leeway=SSE_TOKEN_LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "sub", "habit_id", "scope", "aud", "iss"],
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise ServiceTokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidServiceTokenError() from exc

    if payload.get("scope") != SSE_TOKEN_SCOPE:
        raise InvalidServiceTokenError()
    if payload.get("habit_id") != expected_habit_id:
        raise InvalidServiceTokenError()

    return payload


__all__ = [
    "SSE_TOKEN_AUDIENCE",
    "SSE_TOKEN_SCOPE",
    "SSE_TOKEN_ISSUER",
    "SSE_TOKEN_DEFAULT_TTL_SECONDS",
    "SSE_TOKEN_LEEWAY_SECONDS",
    "generate_sse_token",
    "validate_sse_token",
]
