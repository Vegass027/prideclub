from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.admin import api_router as admin_api_router
from app.api.v1 import (
    admin_suspicious_pairs,
    balance,
    habits,
    health,
    internal_bot,
    internal_checkins,
    internal_payments,
    internal_penalties,
    leaderboard,
    members,
    memberships,
    users,
)
from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import install_middlewares
from app.core.observability import init_sentry
from app.db.redis import close_redis, get_redis


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan: pre-warm Redis на старте, закрыть соединение на shutdown.

    `@app.on_event` deprecated в FastAPI 0.110+ — заменяем на единый
    lifespan-контекст. Преимущества:
    - `yield` отделяет startup от shutdown — нельзя перепутать порядок.
    - Один declaration для обеих фаз — нет риска забыть про одну.
    - TestClient корректно дёргает lifespan при `with TestClient(app)`.
    """
    logger = get_logger("backend.lifespan")

    # Startup: инициализируем Redis-пул сразу, чтобы первый запрос не
    # упёрся в холодный connect (50-200ms). Если Redis недоступен — логируем,
    # но не валим старт приложения: эндпоинты сами решат, как реагировать.
    try:
        redis = get_redis()
        await redis.ping()
        logger.info("backend.startup.redis_ready")
    except Exception as exc:  # noqa: BLE001 — старт должен быть устойчивым
        logger.warning(
            "backend.startup.redis_ping_failed",
            extra={"error": str(exc)},
        )

    logger.info("backend.startup")
    try:
        yield
    finally:
        await close_redis()
        logger.info("backend.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_sentry("backend")

    app = FastAPI(
        title="Habit Club API",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    install_middlewares(app)

    @app.get("/metrics")
    async def metrics() -> Response:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: object, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: object, exc: Exception) -> JSONResponse:
        """Глобальный fallback: любой необработанный сбой → JSON.

        Без этого FastAPI отдаёт HTML/text, клиент (например, бот)
        пытается распарсить как JSON → 500 internal client error.
        С этим хендлером клиент всегда видит `{"code":"internal_error"}`
        и может корректно ретраить/логировать.
        """
        get_logger("main").exception(
            "unhandled_exception", extra={"path": getattr(request, "path", None)}
        )
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "Внутренняя ошибка сервера"},
        )

    # Раздача загруженных медиа (фото клубов)
    import os
    _static_dir = os.environ.get("STATIC_DIR", "/app/static")
    os.makedirs(_static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    app.include_router(health.router, tags=["health"])
    app.include_router(users.router, prefix="/api/v1", tags=["users"])
    app.include_router(habits.router, prefix="/api/v1", tags=["habits"])
    app.include_router(memberships.router, prefix="/api/v1", tags=["memberships"])
    app.include_router(members.router, prefix="/api/v1", tags=["members"])
    app.include_router(balance.router, prefix="/api/v1", tags=["balance"])
    app.include_router(leaderboard.router, prefix="/api/v1", tags=["leaderboard"])
    app.include_router(
        internal_checkins.router, prefix="/internal", tags=["internal"]
    )
    app.include_router(
        internal_bot.router, prefix="/internal", tags=["internal"]
    )
    app.include_router(
        internal_payments.router, prefix="/internal", tags=["internal"]
    )
    app.include_router(
        internal_penalties.router, prefix="/internal", tags=["internal"]
    )
    app.include_router(
        admin_suspicious_pairs.router, prefix="/api/v1", tags=["admin"]
    )
    app.include_router(admin_api_router, prefix="/admin/v1", tags=["admin"])

    return app


app = create_app()


__all__ = ["app", "create_app"]