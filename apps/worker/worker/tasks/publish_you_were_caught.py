"""Pravki-bug-fixes §Z-21 (Item 8): personal you_were_caught event.

После apply_catch на backend'е handler `members.py:catch_violator`
отправляет celery-таску ``publish_you_were_caught`` через `send_task()`
(в ОТДЕЛЬНОМ try/except от publish_catch_event — независимые попытки,
см. комментарий в catch_violator). Worker делает XADD в персональный
user-stream violator'а — ``sse:user:{u}:{h}``.

Idempotency: ключ ``sse_published:caught:{m}:{d}`` (event_type='caught',
COLLISION-фикс из Item 6). Утренний checkin.rejected и вечерний
you_were_caught для одной (m, d) живут в разных namespace'ах,
не блокируют друг друга.

Контракт payload (от backend celery_producer.send_task):
- user_id: int — violator (кому шлём)
- membership_id: str — UUID membership violator'а (для idempotency_key)
- habit_id: str — UUID клуба
- catcher_user_id: int — кто поймал (для UI: «Вас поймал @X»)
- catcher_first_name: str — для UI (Item 9): имя кэтчера
- amount: int — копейки (для UI: «Списан штраф X ₽»)
- date_iso: str — ISO date (для idempotency_key, date_iso part)

Worker fetch (Item 8, Variant C):
- violator_first_name: PK lookup violator по ID — нужно для UI текста
  в personal SSE-фрейме («Вас поймал @catcher_name»).
  +1 query в worker (отдельный процесс, не блокирует HTTP).

АРХИТЕКТУРНО:
- Backend НЕ знает про Redis Streams / EventPublisher / async Redis.
  Variant Б из разведки Item 6.
- Worker — единственная точка публикации.
- If Redis недоступен — return False, НЕ retry (at-most-once, UI-hint).
  Penalty уже в БД — финансовый инвариант цел.

Why no input validation: backend сам формирует payload, доверие к producer.
"""

from __future__ import annotations

from app.core.logging import get_logger

log = get_logger("worker.publish_you_were_caught")


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
        first_name str если найден, иначе None.
    """
    import asyncio

    from app.core.logging import get_logger as _log
    from app.repositories.user_repository import UserRepository

    async def _get() -> str | None:
        from app.db.session import get_session_factory

        async with get_session_factory()() as session:
            repo = UserRepository(session)
            user = await repo.get(violator_user_id)
            return user.first_name if user else None

    try:
        return asyncio.run(_get())
    except Exception as exc:  # noqa: BLE001 — enrichment failure must not break publish
        _log("worker.publish_you_were_caught").warning(
            "violator_first_name_fetch_failed",
            extra={
                "violator_user_id": violator_user_id,
                "err": str(exc),
                "err_type": exc.__class__.__name__,
            },
        )
        return None


async def _run(payload: dict) -> dict:
    """Async-логика personal you_were_caught event'а. Раздельно от task wrapper.

    Returns:
        dict с полями:
        - ok (bool): True если XADD выполнен
        - skipped (bool): True если пропущено по idempotency (duplicate)
        - event_type, membership_id, user_id — echo для трассировки
    """
    from worker.services.event_publisher import EventPublisher

    user_id = payload["user_id"]
    membership_id = payload["membership_id"]
    habit_id = payload["habit_id"]
    date_iso = payload["date_iso"]
    event_type = "caught"  # namespace в idempotency_key для Item 4 COLLISION-фикс

    publisher = _build_production_publisher()
    if publisher is None:
        log.warning(
            "publish_you_were_caught_no_redis",
            extra={
                "user_id": user_id,
                "membership_id": membership_id,
                "habit_id": habit_id,
            },
        )
        return {"ok": False, "skipped": False, "reason": "no_redis_configured"}

    # Item 8: PK lookup violator's first_name (worker fetch, Variant C).
    violator_first_name = _fetch_violator_first_name(user_id)

    checkin_event = {
        "event": "you_were_caught",  # inner XADD event field для UI
        "habit_id": habit_id,
        "catcher_user_id": payload["catcher_user_id"],
        "catcher_first_name": payload.get("catcher_first_name", "User"),
        "violator_first_name": violator_first_name,  # may be None on race
        "amount": payload["amount"],
        "penalty_id": payload.get("penalty_id", ""),  # опционально, для traceability
    }

    # Pravki Item 6: event_type="caught" → idempotency_key namespace
    # 'sse_published:caught:{m}:{d}' (COLLISION-изоляция от checkin).
    ok = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso=date_iso,
        event=type("CheckinEvent", (), {"event": "you_were_caught", "payload": checkin_event})(),
        event_type=event_type,
    )
    return {
        "ok": ok,
        "skipped": not ok,
        "event_type": event_type,
        "user_id": user_id,
        "membership_id": membership_id,
    }


# Celery registration — same pattern as publish_catch_event.py
try:
    from worker.celery_app import celery_app
except ImportError:  # pragma: no cover
    celery_app = None

if celery_app is not None:

    @celery_app.task(
        name="worker.tasks.publish_you_were_caught.run",
        bind=True,
        max_retries=2,
        autoretry_for=(Exception,),  # at-most-once для publish_checkin НЕ retry'ит
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
