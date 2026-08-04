import time
import uuid
from collections.abc import Awaitable, Callable

# NOTE: `from __future__ import annotations` намеренно НЕ используется здесь.
# С ним `Response` становится ForwardRef('Response'), и FastAPI/Pydantic
# падает с "TypeAdapter[Annotated[ForwardRef('Response'), ...]] is not fully
# defined" при попытке построить OpenAPI-схему для middleware. Это известный
# баг FastAPI + Pydantic v2 + PEP 563 forward refs на чужих типах (starlette
# Response, JSONResponse и т.п.).
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
    NotOwnerError,
    ServiceTokenExpiredError,
)
from app.core.logging import get_logger
from app.core.security import validate_init_data, validate_service_token
from app.db.redis import get_redis
from app.services.http_rate_limiter import make_api_v1_limiter, make_internal_limiter

logger = get_logger("auth_middleware")


INTERNAL_PREFIX = "/internal/"
PUBLIC_PREFIX = "/api/v1/"
ADMIN_PREFIX = "/admin/v1/"
HEALTH_PATHS = {"/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}
STATIC_PREFIX = "/static/"  # загруженные медиа (фото клубов) — публичный read-only

# SSE-эндпоинт авторизуется через JWT-токен в query (EventSource в браузере
# не поддерживает кастомные заголовки, поэтому initData не передать).
# Exact match — НЕ префикс: будущие /api/v1/events/* остаются под initData.
# Хендлер /api/v1/events/stream сам валидирует токен и его claim на habit_id.
SSE_AUTH_BYPASS_PATHS = {"/api/v1/events/stream"}


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

        # SSE-эндпоинт авторизуется через токен в query (см. SSE_AUTH_BYPASS_PATHS).
        # Exact match — не префикс.
        if path in SSE_AUTH_BYPASS_PATHS:
            return await call_next(request)

        # Публичные служебные endpoints (k8s probes, OpenAPI) — без аутентификации
        if path in HEALTH_PATHS or path.startswith(STATIC_PREFIX):
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

            elif path.startswith(ADMIN_PREFIX):
                if not settings.owner_telegram_id:
                    return JSONResponse(
                        status_code=503,
                        content={"code": "admin_disabled"},
                    )
                init_data = request.headers.get("X-Telegram-Init-Data")
                if not init_data:
                    raise MissingInitDataError()
                admin_bot_token = settings.bot_token_admin or settings.bot_token
                tg_user = validate_init_data(
                    init_data,
                    admin_bot_token,
                    max_age_seconds=settings.init_data_max_age_seconds,
                )
                if tg_user.id != settings.owner_telegram_id:
                    logger.warning(
                        "admin_auth_rejected",
                        extra={
                            "path": path,
                            "ip": _client_ip(request),
                            "reason": "not_owner",
                            "user_id": tg_user.id,
                        },
                    )
                    raise NotOwnerError()
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
        except NotOwnerError:
            return JSONResponse(
                status_code=403, content={"code": "not_owner"}
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Общий HTTP rate-limit по Redis (60/min для пользователей, 120/min для /internal).

    Только для аутентифицированных эндпоинтов — health/metrics не лимитируем.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        if path in HEALTH_PATHS or path.startswith(STATIC_PREFIX):
            return await call_next(request)

        # SSE-эндпоинт авторизуется через токен в query, request.state.telegram_user
        # не заполнен. Per-connection лимит в MVP не вводим (EventSource в браузере
        # лимитирован ~6 коннекшнами на origin). Если в продакшене понадобится —
        # лимитировать по user_id из claim токена.
        if path in SSE_AUTH_BYPASS_PATHS:
            return await call_next(request)

        try:
            redis = get_redis()
            if path.startswith(PUBLIC_PREFIX) or path.startswith(ADMIN_PREFIX):
                tg_user = getattr(request.state, "telegram_user", None)
                if tg_user is None:
                    return await call_next(request)
                subject = f"u:{tg_user.id}"
                limiter = make_api_v1_limiter(redis)
            elif path.startswith(INTERNAL_PREFIX):
                caller = getattr(request.state, "caller", None)
                if caller is None:
                    return await call_next(request)
                subject = f"s:{caller}"
                limiter = make_internal_limiter(redis)
            else:
                return await call_next(request)

            allowed, count, max_n = await limiter.check(subject)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "rate_limited",
                        "limit": max_n,
                        "window_seconds": limiter._window,
                    },
                    headers={
                        "X-RateLimit-Limit": str(max_n),
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": str(limiter._window),
                    },
                )
        except Exception as exc:  # noqa: BLE001 — fail-open: rate-limit не должен ронять прод
            logger.warning(
                "rate_limit_error",
                extra={"path": path, "error": type(exc).__name__},
            )
            return await call_next(request)

        return await call_next(request)


def install_middlewares(app: FastAPI) -> None:
    """Устанавливает middleware в правильном порядке.

    В FastAPI/Starlette middleware добавляются ПОСЛЕДНИМ — выполняется ПЕРВЫМ.
    Нужный порядок обработки запроса:
        CORS preflight → Auth (401/404) → RateLimit → RequestContext (логи).
    Поэтому регистрируем в обратном порядке:
        RequestContext → RateLimit → Auth → CORS.
    """
    settings = get_settings()
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=[
            "X-Telegram-Init-Data",
            "X-Service-Token",
            "X-Request-ID",
            "Content-Type",
        ],
        max_age=3600,
    )