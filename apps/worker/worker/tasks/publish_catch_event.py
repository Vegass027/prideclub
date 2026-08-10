"""Pravki-bug-fixes §Z-21 (Item 6): broadcast catch_event в habit-stream.

После apply_catch на backend'е (через POST /api/v1/habits/{id}/catch) handler
отправляет celery-таску ``publish_catch_event`` через `send_task()`.
Worker делает один XADD в ``sse:habit:{habit_id}`` — все активные
SSE-клиенты этого клуба получают событие через multiplex (Item 7).

Контракт payload (от backend celery_producer.send_task):
- habit_id: str — UUID клуба
- penalty_id: str — UUID Penalty row (для idempotency_key scope_suffix)
- catcher_user_id: int — кто поймал
- violator_user_id: int — кого поймали
- violator_membership_id: str — для broadcast'а фронт знает кто жертва
- amount: int — копейки

Backend Items 8 (catching victim display) и Item 9 (UI hook) будут
использовать payload для invalidate ["members", "today", "balance"].

АРХИТЕКТУРНО:
- Backend НЕ знает про Redis Streams / EventPublisher / async Redis.
  Он только отправляет задачу через send_task (Pravki-deposit-sse.md §Z-12,
  Variant Б из разведки Item 6).
- Worker — единственная точка публикации. Idempotency через
  EventPublisher.habit_idempotency_key(event_type="habit_catch", unique_id=penalty_id).
  Retry celery-таски (broker reconnect) → duplicate=True → XADD не выполняется.
- If Redis недоступен — return False, НЕ retry (at-most-once, UI-hint).
  Penalty уже в БД — финансовый инвариант цел.

Why no input validation: backend сам формирует payload, доверие к producer.
Validation делается на стороне handler'а (`members.py:catch_violator`).
"""

from __future__ import annotations

from app.core.logging import get_logger

log = get_logger("worker.publish_catch_event")


def _build_production_publisher():
    """Lazy import + lazy creation — обходит циклические импорты при тестах.

    Returns ``None`` если REDIS_URL не задан (тесты с in-memory fakeredis
    создают EventPublisher напрямую с FakeRedis клиентом).
    """
    import os

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    import redis.asyncio as aioredis

    from worker.services.event_publisher import EventPublisher

    return EventPublisher(aioredis.from_url(redis_url, decode_responses=True))


async def _run(payload: dict) -> dict:
    """Async-логика broadcast'а. Раздельно от task wrapper для тестируемости.

    Returns:
        dict с полями:
        - ok (bool): True если XADD выполнен
        - skipped (bool): True если пропущено по idempotency (duplicate)
        - event_type, scope_suffix, habit_id — echo для трассировки
    """
    from worker.services.event_publisher import EventPublisher

    habit_id = payload["habit_id"]
    penalty_id = payload["penalty_id"]
    event_type = "habit_catch"

    publisher = _build_production_publisher()
    if publisher is None:
        log.warning(
            "publish_catch_event_no_redis",
            extra={"habit_id": habit_id, "penalty_id": penalty_id},
        )
        return {"ok": False, "skipped": False, "reason": "no_redis_configured"}

    xadd_payload = {
        "event": "catch",  # inner XADD event field (для UI маппинга)
        "habit_id": habit_id,
        "catcher_user_id": payload["catcher_user_id"],
        "violator_user_id": payload["violator_user_id"],
        "violator_membership_id": payload["violator_membership_id"],
        "amount": payload["amount"],
        "penalty_id": penalty_id,
    }

    ok = await publisher.publish_to_habit(
        habit_id=habit_id,
        event_type=event_type,
        payload=xadd_payload,
        scope_suffix=penalty_id,
    )
    return {
        "ok": ok,
        "skipped": not ok,  # True если duplicate
        "event_type": event_type,
        "scope_suffix": penalty_id,
        "habit_id": habit_id,
    }


# Celery registration — same pattern as process_checkin.py (декоратор применяется
# только если celery_app доступен; иначе модуль импортируется в тестах без broker).
try:
    from worker.celery_app import celery_app
except ImportError:  # pragma: no cover
    celery_app = None

if celery_app is not None:

    @celery_app.task(
        name="worker.tasks.publish_catch_event.run",
        bind=True,
        max_retries=2,
        autoretry_for=(Exception,),  # at-most-once для publish_to_habit НЕ retry'ит, но transient broker errors retry
        retry_backoff=True,
        retry_backoff_max=30,
        retry_jitter=True,
    )
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        """Celery entrypoint.

        Аргумент: payload (dict) от backend celery_producer.send_task.
        Returns: dict с результатом _run (для celery result backend).
        """
        import asyncio

        return asyncio.run(_run(payload))


__all__ = ["run", "_run", "_build_production_publisher"]
