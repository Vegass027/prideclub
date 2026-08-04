"""Unit tests for SSE token sign/verify (apps/backend/app/services/sse/sse_token.py).

Чистая логика JWT — без БД, без Redis, без FastAPI. Покрывает:
- roundtrip sign/verify
- expired token
- wrong secret
- wrong audience
- wrong scope
- habit_id mismatch между токеном и expected
- empty secret на стороне sign
"""
from __future__ import annotations

import time

import pytest


def test_roundtrip_sign_and_verify() -> None:
    from app.services.sse.sse_token import (
        generate_sse_token,
        validate_sse_token,
    )

    secret = "sse-test-secret"
    fixed_now = int(time.time())
    token, exp = generate_sse_token(
        user_id=12345, habit_id="habit-uuid-1", secret=secret, now=fixed_now
    )
    assert isinstance(token, str)
    assert exp == fixed_now + 60

    payload = validate_sse_token(token, secret=secret, expected_habit_id="habit-uuid-1")
    assert payload["sub"] == "12345"
    assert payload["habit_id"] == "habit-uuid-1"
    assert payload["scope"] == "sse:today"
    assert payload["aud"] == "sse-stream"
    assert payload["iss"] == "backend"
    assert payload["iat"] == fixed_now
    assert payload["exp"] == fixed_now + 60


def test_empty_secret_raises() -> None:
    from app.services.sse.sse_token import generate_sse_token

    with pytest.raises(ValueError, match="SSE_TOKEN_SECRET"):
        generate_sse_token(user_id=1, habit_id="h", secret="")


def test_wrong_secret_raises_invalid_token() -> None:
    from app.core.exceptions import InvalidServiceTokenError
    from app.services.sse.sse_token import (
        generate_sse_token,
        validate_sse_token,
    )

    token, _ = generate_sse_token(user_id=1, habit_id="h", secret="good-secret")
    with pytest.raises(InvalidServiceTokenError):
        validate_sse_token(token, secret="wrong-secret", expected_habit_id="h")


def test_expired_token_raises_service_token_expired() -> None:
    from app.core.exceptions import ServiceTokenExpiredError
    from app.services.sse.sse_token import (
        generate_sse_token,
        validate_sse_token,
    )

    # iat=now-300, ttl=60 → exp=now-240, давно истёк.
    secret = "s"
    token, _ = generate_sse_token(
        user_id=1,
        habit_id="h",
        secret=secret,
        ttl_seconds=60,
        now=int(time.time()) - 300,
    )
    with pytest.raises(ServiceTokenExpiredError):
        validate_sse_token(token, secret=secret, expected_habit_id="h")


def test_habit_id_mismatch_raises_invalid_token() -> None:
    """Токен выдан на habit_id=A, а в query пришёл habit_id=B → отказ.

    Это закрывает атаку 'переиграть чужой токен': даже если атакующий
    перехватил токен, выданный на habit_A, он не сможет слушать habit_B
    (даже если узнал user_id из sub — токен привязан к habit_id).
    """
    from app.core.exceptions import InvalidServiceTokenError
    from app.services.sse.sse_token import (
        generate_sse_token,
        validate_sse_token,
    )

    token, _ = generate_sse_token(user_id=1, habit_id="habit-A", secret="s")
    with pytest.raises(InvalidServiceTokenError):
        validate_sse_token(token, secret="s", expected_habit_id="habit-B")


def test_wrong_audience_rejected() -> None:
    """Токен с aud != 'sse-stream' (например, service-token) → отказ.

    Защита от использования service-токена как SSE-токена (разные секреты
    тоже защищают, но это дополнительный слой на уровне claims).
    """
    import jwt as pyjwt

    from app.core.exceptions import InvalidServiceTokenError
    from app.services.sse.sse_token import validate_sse_token

    # Подписываем токен с aud="backend-api" (как у service-token).
    payload = {
        "sub": "1",
        "habit_id": "h",
        "scope": "sse:today",
        "aud": "backend-api",  # ← неправильный audience
        "iss": "backend",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = pyjwt.encode(payload, "s", algorithm="HS256")
    with pytest.raises(InvalidServiceTokenError):
        validate_sse_token(token, secret="s", expected_habit_id="h")


def test_wrong_scope_rejected() -> None:
    """scope != 'sse:today' → отказ (защита от scope confusion)."""
    import jwt as pyjwt

    from app.core.exceptions import InvalidServiceTokenError
    from app.services.sse.sse_token import validate_sse_token

    payload = {
        "sub": "1",
        "habit_id": "h",
        "scope": "admin:everything",  # ← неправильный scope
        "aud": "sse-stream",
        "iss": "backend",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = pyjwt.encode(payload, "s", algorithm="HS256")
    with pytest.raises(InvalidServiceTokenError):
        validate_sse_token(token, secret="s", expected_habit_id="h")


def test_token_within_leeway_accepted() -> None:
    """Токен с exp, который только что истёк (в пределах leeway) — принимается.

    Leeway нужен для устойчивости к дрейфу часов между контейнерами и для
    reconnect-флоу (useTodayStream открывает новый EventSource на onerror,
    новый токен получается с небольшой задержкой после старого). Без leeway
    можно получить ложный 401 на границе exp.
    """
    from app.services.sse.sse_token import (
        SSE_TOKEN_LEEWAY_SECONDS,
        generate_sse_token,
        validate_sse_token,
    )

    now = int(time.time())
    # Токен истёк 5с назад — в пределах leeway (10с), должен пройти.
    token, _ = generate_sse_token(
        user_id=1, habit_id="h", secret="s", ttl_seconds=60, now=now - 65
    )
    # Не мокаем time — проверяем реальное поведение jwt.decode с leeway.
    payload = validate_sse_token(token, secret="s", expected_habit_id="h")
    assert payload["sub"] == "1"

    # Sanity: leeway константа разумная (не 0, не >60).
    assert 0 < SSE_TOKEN_LEEWAY_SECONDS <= 60
