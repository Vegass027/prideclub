"""Celery producer — отправка задач из backend в очередь.

Backend НЕ запускает Celery worker (worker — отдельный контейнер). Но backend
кладёт задачи в ту же очередь через `app.send_task(...)` — это штатный
механизм Celery для вызова тасок по имени без их прямого импорта (см. FAQ:
https://docs.celeryq.dev/en/stable/faq.html#call-a-task-by-name).

Протокол: backend создаёт Celery-инстанс с тем же broker URL, что и worker,
но НЕ регистрирует таски (`include=[]`). Worker-процесс, который слушает
тот же broker, забирает сообщения по `task_name` и исполняет.
"""
from __future__ import annotations

from typing import Any

from celery import Celery

from app.core.config import get_settings
from app.core.logging import get_logger


_TASK_NAMES: dict[str, str] = {
    "checkin": "worker.tasks.process_checkin.run",
    "penalty": "worker.tasks.process_penalty.run",
    "payment": "worker.tasks.process_payment.run",
}


_app: Celery | None = None


def _get_app() -> Celery:
    """Lazy Celery-инстанс для backend.

    Важно: `include=[]` — backend НЕ должен импортировать worker-таски,
    иначе при старте API подтянется всё дерево зависимостей воркера.
    """
    global _app
    if _app is None:
        settings = get_settings()
        broker = settings.celery_broker_url or settings.redis_url
        _app = Celery(
            "habit_club_backend_producer",
            broker=broker,
            backend=settings.celery_result_backend,
            include=[],  # никаких автоимпортов тасок
        )
        _app.conf.update(
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            task_acks_late=True,
            task_reject_on_worker_lost=True,
            broker_connection_retry_on_startup=True,
            timezone="UTC",
        )
    return _app


def send_task(task_kind: str, payload: dict[str, Any]) -> str:
    """Кладёт задачу в Celery-очередь.

    Args:
        task_kind: один из 'checkin' | 'penalty' | 'payment'.
        payload: dict, передаётся в `kwargs={'payload': payload}` worker-таску.

    Returns:
        task_id (str) — UUID, который можно использовать для трекинга.

    Raises:
        ValueError: если task_kind неизвестен.
        kombu.exceptions.OperationalError: если брокер недоступен.
    """
    if task_kind not in _TASK_NAMES:
        raise ValueError(f"Unknown task kind: {task_kind!r}")
    task_name = _TASK_NAMES[task_kind]

    log = get_logger("celery_producer")
    result = _get_app().send_task(task_name, kwargs={"payload": payload})
    log.info(
        "celery_task_enqueued",
        extra={"task_name": task_name, "task_id": result.id},
    )
    return result.id


__all__ = ["send_task"]