from __future__ import annotations

import pytest


def test_init_data_valid() -> None:
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    from app.core.security import validate_init_data

    bot_token = "test-bot-token"
    user = {"id": 12345, "first_name": "Test", "username": "tester", "is_premium": False}
    auth_date = int(time.time())
    params = {"user": json.dumps(user, separators=(",", ":")), "auth_date": str(auth_date)}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    init_data = urlencode(params)

    tg = validate_init_data(init_data, bot_token)
    assert tg.id == 12345
    assert tg.first_name == "Test"


def test_init_data_invalid_hash() -> None:
    from app.core.security import validate_init_data
    from app.core.exceptions import InvalidInitDataError

    with pytest.raises(InvalidInitDataError):
        validate_init_data("hash=invalid&auth_date=1&user={}", "bot-token")


def test_service_token_roundtrip() -> None:
    from app.core.security import generate_service_token, validate_service_token

    secret = "service-secret"
    token = generate_service_token(
        service_name="bot", target_audience="backend-api", secret=secret, ttl_seconds=60
    )
    payload = validate_service_token(token, secret=secret, expected_audience="backend-api")
    assert payload["service"] == "bot"


def test_service_token_wrong_aud() -> None:
    from app.core.security import generate_service_token, validate_service_token
    from app.core.exceptions import InvalidServiceTokenError

    secret = "service-secret"
    token = generate_service_token(service_name="bot", target_audience="backend-api", secret=secret)
    with pytest.raises(InvalidServiceTokenError):
        validate_service_token(token, secret=secret, expected_audience="other-service")