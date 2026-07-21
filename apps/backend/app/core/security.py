from __future__ import annotations

import os
import sys

# Подключаем монорепо-пакет `packages/shared` без необходимости pip-install.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
_SHARED_PARENT = os.path.join(_REPO_ROOT, "packages")
_SHARED = os.path.join(_REPO_ROOT, "packages", "shared")
for _p in (_SHARED_PARENT, _SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from security import (  # noqa: E402  (sys.path настраивается выше)
    InitDataExpiredError as _InitDataExpiredError,
)
from security import (
    InvalidInitDataError as _InvalidInitDataError,
)
from security import (
    InvalidServiceTokenError as _InvalidServiceTokenError,
)
from security import (
    ServiceTokenExpiredError as _ServiceTokenExpiredError,
)
from security import (
    TelegramUser,
    generate_service_token,
)
from security import (
    validate_init_data as _validate_init_data,
)
from security import (
    validate_service_token as _validate_service_token,
)

from app.core.exceptions import (
    InitDataExpiredError,
    InvalidInitDataError,
    InvalidServiceTokenError,
    ServiceTokenExpiredError,
)


def validate_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 86400):
    """Парсит и подтверждает подпись X-Telegram-Init-Data.

    Бросает доменные ошибки:
    - InvalidInitDataError — битая подпись / невалидный user
    - InitDataExpiredError — auth_date старше max_age_seconds
    """
    try:
        return _validate_init_data(
            init_data, bot_token, max_age_seconds=max_age_seconds
        )
    except _InvalidInitDataError as exc:
        raise InvalidInitDataError() from exc
    except _InitDataExpiredError as exc:
        raise InitDataExpiredError() from exc


def validate_service_token(token: str, *, secret: str, expected_audience: str):
    """Валидирует service-token JWT (HS256).

    Бросает:
    - InvalidServiceTokenError — битая подпись / aud не совпал / нет обязательных claims
    - ServiceTokenExpiredError — exp истёк
    """
    try:
        return _validate_service_token(
            token, secret=secret, expected_audience=expected_audience
        )
    except _InvalidServiceTokenError as exc:
        raise InvalidServiceTokenError() from exc
    except _ServiceTokenExpiredError as exc:
        raise ServiceTokenExpiredError() from exc


__all__ = [
    "TelegramUser",
    "InvalidInitDataError",
    "InitDataExpiredError",
    "InvalidServiceTokenError",
    "ServiceTokenExpiredError",
    "validate_init_data",
    "validate_service_token",
    "generate_service_token",
]
