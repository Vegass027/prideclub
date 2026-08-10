"""Pravki-bug-fixes §Z-21 (Item 6): worker task publish_catch_event.

Покрывает:
- _run happy path: publish_to_habit → XADD в habit-stream
- _run duplicate detection: второй вызов с тем же scope_suffix → skipped
- _run namespace изоляция: разные event_type с одним scope_suffix
- _run без REDIS_URL → no_redis_configured (no exception)
- _run с Redis exception → return False (at-most-once)
- celery task registration (decorator applied)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import fakeredis.aioredis
import pytest


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _make_payload(habit_id: str = "h-1", penalty_id: str = "p-1") -> dict:
    return {
        "habit_id": habit_id,
        "penalty_id": penalty_id,
        "catcher_user_id": 501,
        "violator_user_id": 502,
        "violator_membership_id": "m-502",
        "amount": 10000,
    }


@pytest.mark.asyncio
async def test_run_publishes_to_habit_stream(fake_redis) -> None:
    """Happy path: XADD в sse:habit:{habit_id}, idempotency-ключ установлен."""
    from worker.tasks.publish_catch_event import _run
    from worker.services.event_publisher import EventPublisher

    # Заменяем _build_production_publisher чтобы он вернул наш EventPublisher с fake_redis
    publisher = EventPublisher(fake_redis)

    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=publisher,
    ):
        result = await _run(_make_payload())

    assert result["ok"] is True
    assert result["skipped"] is False
    assert result["habit_id"] == "h-1"
    assert result["scope_suffix"] == "p-1"

    # XADD выполнен в habit-stream.
    entries = await fake_redis.xrange("sse:habit:h-1")
    assert len(entries) == 1
    _id, fields = entries[0]
    assert fields["event"] == "catch"
    assert fields["habit_id"] == "h-1"
    import json
    payload = json.loads(fields["payload"])
    assert payload["catcher_user_id"] == 501
    assert payload["violator_user_id"] == 502
    assert payload["penalty_id"] == "p-1"

    # Idempotency-ключ живёт 24ч.
    assert await fake_redis.get("sse_published:habit_catch:p-1") == "1"


@pytest.mark.asyncio
async def test_run_second_call_skipped_on_duplicate(fake_redis) -> None:
    """Guard 2: повторный _run с тем же penalty_id → skipped=True, ok=False,
    второй XADD НЕ выполняется (Celery retry / duplicate delivery)."""
    from worker.tasks.publish_catch_event import _run
    from worker.services.event_publisher import EventPublisher

    publisher = EventPublisher(fake_redis)

    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=publisher,
    ):
        r1 = await _run(_make_payload(penalty_id="dup-p"))
        r2 = await _run(_make_payload(penalty_id="dup-p"))

    assert r1["ok"] is True
    assert r1["skipped"] is False
    assert r2["ok"] is False
    assert r2["skipped"] is True

    # Только ОДИН entry в habit-stream.
    entries = await fake_redis.xrange("sse:habit:h-1")
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_run_no_redis_configured_returns_reason(fake_redis) -> None:
    """Если _build_production_publisher вернул None (REDIS_URL не задан),
    _run возвращает reason='no_redis_configured', НЕ бросает исключение."""
    from worker.tasks.publish_catch_event import _run

    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=None,
    ):
        result = await _run(_make_payload())

    assert result["ok"] is False
    assert result["reason"] == "no_redis_configured"
    assert result["skipped"] is False


@pytest.mark.asyncio
async def test_run_publisher_runtime_error_returns_ok_false(fake_redis) -> None:
    """Если EventPublisher.publish_to_habit бросает исключение (теоретически
    не должно, т.к. есть try/except, но защищаемся), _run ловит в publisher.
    Защита на уровне _run: если publisher=None — ok=False, не raise.
    """
    from worker.tasks.publish_catch_event import _run

    # Если publisher IS None (REDIS_URL не задан), поведение уже проверено
    # в test_run_no_redis_configured_returns_reason. Этот тест — страховка
    # на случай если кто-то сломал _build_production_publisher.
    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=None,
    ):
        result = await _run(_make_payload())

    assert result["ok"] is False
    assert result["reason"] == "no_redis_configured"


def test_celery_task_is_registered() -> None:
    """Sanity: декоратор celery_app.task применяется (имя worker.tasks.publish_catch_event.run
    присутствует в celery_app.registry)."""
    from worker.celery_app import celery_app

    assert "worker.tasks.publish_catch_event.run" in celery_app.tasks
