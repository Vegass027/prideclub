from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.constants import CharacterConfig
from worker.config import get_settings
from worker.logging_setup import configure_logging


_settings = get_settings()
configure_logging(_settings.log_level)


def _init_observability() -> None:
    """Sentry init для worker. No-op если SENTRY_DSN пуст."""
    if not _settings.sentry_dsn:
        return
    import sentry_sdk

    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=_settings.sentry_dsn,
        environment=_settings.environment if hasattr(_settings, "environment") else "production",
        integrations=[CeleryIntegration()],
        traces_sample_rate=0.1,
        server_name="habit-club-worker",
    )


_init_observability()


celery_app = Celery(
    "habit_club",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=[
        "worker.tasks.process_checkin",
        "worker.tasks.close_catch_window",
        "worker.tasks.process_penalty",
        "worker.tasks.process_payment",
        "worker.tasks.close_season",
        "worker.tasks.update_user_photos",
        # Pravki-bug-fixes §Z-21 (Item 6): broadcast catch_event
        # в habit-stream через EventPublisher.publish_to_habit.
        "worker.tasks.publish_catch_event",
        # Pravki-bug-fixes §Z-21 (Item 8): personal you_were_caught
        # в user-stream через EventPublisher.publish_checkin
        # (event_type='caught' → COLLISION-изоляция).
        "worker.tasks.publish_you_were_caught",
        # Phase 3 v2 Task 3.5: cron freeze inactive stats (30 дней без чек-инов).
        "worker.tasks.freeze_inactive_stats",
    ],
)

celery_app.conf.update(
    timezone="UTC",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=120,
    task_soft_time_limit=90,
    worker_prefetch_multiplier=4,
    broker_connection_retry_on_startup=True,
)


celery_app.conf.beat_schedule = {
    "close_catch_window_hourly": {
        "task": "worker.tasks.close_catch_window.run_for_active_habits",
        # Каждый час в :05 — после типичного окончания окон чек-ина.
        # Сама таска skip'ает клубы с ещё-открытым окном (защита от раннего штрафа).
        "schedule": crontab(minute=5),
    },
    "close_season_daily": {
        "task": "worker.tasks.close_season.run",
        "schedule": crontab(hour=5, minute=0),
    },
    "update_user_photos_daily": {
        # Раз в сутки подтягиваем file_id аватарок активных юзеров.
        # На 1000 users = 2000 req к Bot API = 0.023 req/sec, в 1300 раз
        # ниже лимита 30/sec. Время выбрано после close_season, чтобы
        # не пересекаться с другими cron'ами (см. docs/02-architecture.md §2).
        "task": "worker.tasks.update_user_photos.run",
        "schedule": crontab(hour=6, minute=0),
    },
    "freeze_inactive_stats_daily": {
        # Phase 3 v2 Task 3.5: ежедневная заморозка неактивных характеристик.
        # ⚠️ НЕ литералы 4 — читаем из CharacterConfig.
        # Запускаем до close_catch_window (04:00 vs hourly :05), чтобы
        # новые штрафы в этот день не учитывали fresh-frozen users
        # для streak-recency purposes.
        "task": "worker.tasks.freeze_inactive_stats.run",
        "schedule": crontab(
            hour=CharacterConfig.FREEZE_CRON_HOUR_UTC,
            minute=0,
        ),
    },
}