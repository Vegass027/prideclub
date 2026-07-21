"""Sentry + Prometheus инициализация.

Sentry SDK подключается ТОЛЬКО если задан SENTRY_DSN — иначе init no-op.
Prometheus: app-scoped метрики через prometheus_client (HTTP, DB).
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger


def init_sentry(component: str) -> None:
    """Инициализирует Sentry для backend/worker если DSN задан.

    Args:
        component: 'backend' | 'worker' — для тега в Sentry.
    """
    settings = get_settings()
    dsn = settings.sentry_dsn
    if not dsn:
        return

    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        traces_sample_rate=0.1 if settings.environment == "production" else 0.0,
        profiles_sample_rate=0.1 if settings.environment == "production" else 0.0,
        server_name=f"habit-club-{component}",
    )
    log = get_logger(component)
    log.info("sentry_initialized", extra={"dsn_set": bool(dsn)})


__all__ = ["init_sentry"]