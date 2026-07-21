from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import (
    admin_suspicious_pairs,
    balance,
    habits,
    health,
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
from app.db.session import get_session


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    init_sentry("backend")
    logger = get_logger("backend.main")

    app = FastAPI(
        title="Habit Club API",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    install_middlewares(app)

    @app.get("/metrics")
    async def metrics() -> "Response":  # type: ignore[name-defined]
        from fastapi.responses import Response
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: object, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )

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
        internal_payments.router, prefix="/internal", tags=["internal"]
    )
    app.include_router(
        internal_penalties.router, prefix="/internal", tags=["internal"]
    )
    app.include_router(
        admin_suspicious_pairs.router, prefix="/api/v1", tags=["admin"]
    )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await close_redis()
        logger.info("backend.shutdown")

    return app


app = create_app()


__all__ = ["app", "create_app"]