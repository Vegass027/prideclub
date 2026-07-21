from __future__ import annotations

import json
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import (
    InitDataExpiredError,
    InvalidInitDataError,
    InvalidServiceTokenError,
    MissingInitDataError,
    MissingServiceTokenError,
    ServiceTokenExpiredError,
)
from app.core.logging import get_logger
from app.core.security import validate_init_data, validate_service_token


logger = get_logger("auth_middleware")


INTERNAL_PREFIX = "/internal/"
PUBLIC_PREFIX = "/api/v1/"
HEALTH_PATHS = {"/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class AuthMiddleware(BaseHTTPMiddleware):
    """Маршрутизирует трафик по двум контурам доверия.

    - /internal/* → X-Service-Token (JWT).
    - /api/v1/*   → X-Telegram-Init-Data.
    - всё остальное → 404.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        settings = get_settings()

        # Публичные служебные endpoints (k8s probes, OpenAPI) — без аутентификации
        if path in HEALTH_PATHS:
            return await call_next(request)

        try:
            if path.startswith(INTERNAL_PREFIX):
                token = request.headers.get("X-Service-Token")
                if not token:
                    raise MissingServiceTokenError()
                payload = validate_service_token(
                    token,
                    secret=settings.service_secret,
                    expected_audience="backend-api",
                )
                request.state.caller = payload["service"]

            elif path.startswith(PUBLIC_PREFIX):
                init_data = request.headers.get("X-Telegram-Init-Data")
                if not init_data:
                    raise MissingInitDataError()
                tg_user = validate_init_data(
                    init_data,
                    settings.bot_token,
                    max_age_seconds=settings.init_data_max_age_seconds,
                )
                request.state.telegram_user = tg_user

            else:
                return JSONResponse(
                    status_code=404, content={"code": "not_found"}
                )

        except MissingServiceTokenError:
            logger.warning(
                "auth_failed",
                extra={"path": path, "ip": _client_ip(request), "reason": "missing_service_token"},
            )
            return JSONResponse(
                status_code=401, content={"code": "missing_service_token"}
            )
        except MissingInitDataError:
            logger.warning(
                "auth_failed",
                extra={"path": path, "ip": _client_ip(request), "reason": "missing_init_data"},
            )
            return JSONResponse(
                status_code=401, content={"code": "missing_init_data"}
            )
        except InvalidInitDataError:
            logger.warning(
                "auth_failed",
                extra={"path": path, "ip": _client_ip(request), "reason": "invalid_init_data"},
            )
            return JSONResponse(
                status_code=401, content={"code": "invalid_init_data"}
            )
        except InitDataExpiredError:
            logger.warning(
                "auth_failed",
                extra={"path": path, "ip": _client_ip(request), "reason": "init_data_expired"},
            )
            return JSONResponse(
                status_code=401, content={"code": "init_data_expired"}
            )
        except InvalidServiceTokenError:
            logger.warning(
                "auth_failed",
                extra={"path": path, "ip": _client_ip(request), "reason": "invalid_service_token"},
            )
            return JSONResponse(
                status_code=401, content={"code": "invalid_service_token"}
            )
        except ServiceTokenExpiredError:
            return JSONResponse(
                status_code=401, content={"code": "service_token_expired"}
            )

        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Добавляет request_id и измеряет latency."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex)
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)

        response.headers["X-Request-ID"] = request_id

        tg_user = getattr(request.state, "telegram_user", None)
        caller = getattr(request.state, "caller", None)

        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "user_id": tg_user.id if tg_user else None,
                "caller": caller,
            },
        )
        return response


def install_middlewares(app: FastAPI) -> None:
    """Устанавливает middleware в правильном порядке.

    В FastAPI/Starlette middleware добавляются ПОСЛЕДНИМ — выполняются ПЕРВЫМ.
    Нужный порядок обработки запроса:
        CORS preflight → Auth (401/404) → RequestContext (логи).
    Поэтому регистрируем в обратном порядке: RequestContext → Auth → CORS.
    """
    settings = get_settings()
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "X-Telegram-Init-Data",
            "X-Service-Token",
            "X-Request-ID",
            "Content-Type",
        ],
        max_age=3600,
    )