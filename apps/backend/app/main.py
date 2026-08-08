from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.admin import api_router as admin_api_router
from app.api.v1 import (
    admin_suspicious_pairs,
    balance,
    events,
    habits,
    health,
    internal_bot,
    internal_checkins,
    internal_payments,
    internal_penalties,
    leaderboard,
    members,
    memberships,
    payments,
    users,
)
from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import install_middlewares
from app.core.observability import init_sentry
from app.db.redis import close_redis, get_redis
from app.db.redis_async import close_async_redis, get_async_redis


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan: pre-warm Redis + aiohttp session на старте, закрыть на shutdown.

    `@app.on_event` deprecated в FastAPI 0.110+ — заменяем на единый
    lifespan-контекст. Преимущества:
    - `yield` отделяет startup от shutdown — нельзя перепутать порядок.
    - Один declaration для обеих фаз — нет риска забыть про одну.
    - TestClient корректно дёргает lifespan при `with TestClient(app)`.
    """
    from app.core.telegram_bot_api import close_session, make_session

    logger = get_logger("backend.lifespan")

    # Startup: aiohttp.ClientSession для исходящих к Telegram Bot API.
    # Создаём в lifespan (где есть event loop), кладём в app.state.
    # DI-провайдеры (get_avatar_service, notification_service) читают
    # оттуда. Нельзя создавать в DI — там нет event loop.
    app.state.bot_http = make_session()
    logger.info("backend.startup.bot_http_ready")

    # Startup: директория для локального кеша аватарок (Pravki.md §7.1 v3).
    # Создаём в lifespan (sync I/O в thread, не блокируем event loop).
    # <STATIC_DIR>/avatars/{user_id}.jpg — file_id хранится в БД, Redis
    # кеширует file_id на 6ч для инвалидации при смене фото.
    static_dir = Path(os.environ.get("STATIC_DIR", "/app/static"))
    avatars_dir = static_dir / "avatars"
    await asyncio.to_thread(avatars_dir.mkdir, parents=True, exist_ok=True)
    app.state.avatars_dir = avatars_dir
    logger.info("backend.startup.avatars_dir_ready", extra={"path": str(avatars_dir)})

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

    # Startup: то же для async-Redis singleton из db/redis_async.py.
    # Нужен для SSE XREAD в RedisStreamBus — connection pool живёт
    # один на процесс, иначе на каждом SSE-открытии создавалась бы
    # новая фабрика `redis.asyncio.from_url()` (PostReview fix от 2026-08-04,
    # см. блокер в ревью Step 4 — FD-leak при reconnect-loop).
    try:
        async_redis = get_async_redis()
        await async_redis.ping()
        logger.info("backend.startup.async_redis_ready")
    except Exception as exc:  # noqa: BLE001 — старт должен быть устойчивым
        logger.warning(
            "backend.startup.async_redis_ping_failed",
            extra={"error": str(exc)},
        )

    logger.info("backend.startup")
    try:
        yield
    finally:
        await close_redis()
        await close_async_redis()
        await close_session(app.state.bot_http)
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
        # Pravki-deposit-sse.md §Z-3.2: InsufficientDepositError требует
        # передать клиенту required_kopecks / current_kopecks / club_penalty_kopecks
        # для UI-модала «Недостаточно средств». Все DomainError'ы могут
        # передавать extras через **kwargs в __init__ — глобальный handler
        # мерджит их в content (рядом с code/message).
        content: dict = {"code": exc.code, "message": exc.message}
        if getattr(exc, "extras", None):
            content.update(exc.extras)
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
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
    app.include_router(payments.router, prefix="/api/v1", tags=["payments"])
    app.include_router(leaderboard.router, prefix="/api/v1", tags=["leaderboard"])
    app.include_router(events.router, prefix="/api/v1", tags=["events"])
    app.include_router(internal_checkins.router, prefix="/internal", tags=["internal"])
    app.include_router(internal_bot.router, prefix="/internal", tags=["internal"])
    app.include_router(internal_payments.router, prefix="/internal", tags=["internal"])
    app.include_router(internal_penalties.router, prefix="/internal", tags=["internal"])
    app.include_router(admin_suspicious_pairs.router, prefix="/api/v1", tags=["admin"])
    app.include_router(admin_api_router, prefix="/admin/v1", tags=["admin"])

    return app


app = create_app()


__all__ = ["app", "create_app"]
