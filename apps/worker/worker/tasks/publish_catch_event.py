"""Pravki-bug-fixes §Z-21 (Item 6 + Item 8): broadcast catch_event в habit-stream.

После apply_catch на backend'е (через POST /api/v1/habits/{id}/catch) handler
отправляет celery-таску ``publish_catch_event`` через `send_task()`.
Worker делает один XADD в ``sse:habit:{habit_id}`` — все активные
SSE-клиенты этого клуба получают событие через multiplex (Item 7).

Контракт payload (от backend celery_producer.send_task):
- habit_id: str — UUID клуба
- penalty_id: str — UUID Penalty row (для idempotency_key scope_suffix)
- catcher_user_id: int — кто поймал
- catcher_first_name: str — для UI (Item 9) — кэтчер показывается в Members/Today
- violator_user_id: int — кого поймали
- violator_membership_id: str — для broadcast'а фронт знает кто жертва
- amount: int — копейки

Worker fetch (Item 8, Variant C из разведки):
- violator_first_name: int — PK lookup в `users` по violator_user_id.
  Backend НЕ делает этот fetch (выбран Вариант C чтобы держать backend
  image-light). +1 query в worker (отдельный процесс, не блокирует
  HTTP response).

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


def _fetch_violator_first_name(violator_user_id: int) -> str | None:
    """PK lookup violator по ID. Отдельный worker fetch (Variant C).

    Returns:
        first_name str если найден, иначе None (юзер удалён между
        apply_catch и этой таской — крайне маловероятный race).
    """
    import asyncio

    from app.core.logging import get_logger as _log
    from app.repositories.user_repository import UserRepository

    async def _get() -> str | None:
        # Worker НЕ имеет постоянной DB-сессии (нет async_session_factory
        # в worker процессе — celery task живёт в одной короткой корутине).
        # Создаём сессию on-demand, закрываем после запроса.
        from app.db.session import get_session_factory

        async with get_session_factory()() as session:
            repo = UserRepository(session)
            user = await repo.get(violator_user_id)
            return user.first_name if user else None

    try:
        return asyncio.run(_get())
    except Exception as exc:  # noqa: BLE001 — enrichment failure must not break publish
        _log("worker.publish_catch_event").warning(
            "violator_first_name_fetch_failed",
            extra={
                "violator_user_id": violator_user_id,
                "err": str(exc),
                "err_type": exc.__class__.__name__,
            },
        )
        return None


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

    # Item 8: PK lookup violator's first_name (отдельный worker fetch).
    # Failure в этом lookup'е не должен ломать publish — log warning, payload
    # без violator_first_name (UI fallback на user_id).
    violator_first_name = _fetch_violator_first_name(payload["violator_user_id"])

    xadd_payload = {
        "event": "catch",  # inner XADD event field (для UI маппинга)
        "habit_id": habit_id,
        "catcher_user_id": payload["catcher_user_id"],
        "catcher_first_name": payload.get("catcher_first_name", "User"),
        "violator_user_id": payload["violator_user_id"],
        "violator_first_name": violator_first_name,  # may be None on race
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


__all__ = ["run", "_run", "_build_production_publisher", "_fetch_violator_first_name"]
