from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

import jwt


@dataclass(frozen=True, slots=True)
class TelegramUser:
    id: int
    first_name: str
    last_name: str | None
    username: str | None
    language_code: str | None
    is_premium: bool
    auth_date: int


class InvalidInitDataError(Exception):
    pass


class InitDataExpiredError(Exception):
    pass


class InvalidServiceTokenError(Exception):
    pass


class ServiceTokenExpiredError(Exception):
    pass


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> TelegramUser:
    if not init_data:
        raise InvalidInitDataError()
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise InvalidInitDataError()
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        raise InvalidInitDataError()
    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > max_age_seconds:
        raise InitDataExpiredError()
    raw_user = parsed.get("user", "{}")
    try:
        user_obj: dict[str, Any] = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise InvalidInitDataError("user field is not valid json") from exc
    return TelegramUser(
        id=int(user_obj["id"]),
        first_name=str(user_obj.get("first_name", "")),
        last_name=user_obj.get("last_name"),
        username=user_obj.get("username"),
        language_code=user_obj.get("language_code"),
        is_premium=bool(user_obj.get("is_premium", False)),
        auth_date=auth_date,
    )


def generate_service_token(
    *,
    service_name: str,
    target_audience: str,
    secret: str,
    ttl_seconds: int = 60,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "service": service_name,
        "iss": service_name,
        "aud": target_audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def validate_service_token(
    token: str,
    *,
    secret: str,
    expected_audience: str,
) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=expected_audience,
            options={"leeway": 30},
            required=["exp", "iat", "service", "aud", "iss"],
        )
    except jwt.ExpiredSignatureError as exc:
        raise ServiceTokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidServiceTokenError() from exc