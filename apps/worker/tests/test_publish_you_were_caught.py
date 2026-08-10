"""Pravki-bug-fixes §Z-21 (Item 8): worker task publish_you_were_caught.

Покрывает:
- Test 4 (mandatory): _run использует publish_checkin с event_type='caught',
  ключ идемпотентности namespace-изолирован от checkin.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import fakeredis.aioredis
import pytest

from worker.services.event_publisher import EventPublisher


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def publisher(fake_redis) -> EventPublisher:
    return EventPublisher(fake_redis)


def _payload(**overrides) -> dict:
    base = {
        "user_id": 42,
        "membership_id": "11111111-2222-3333-4444-555555555555",
        "habit_id": "66666666-7777-8888-9999-aaaaaaaaaaaa",
        "catcher_user_id": 7,
        "catcher_first_name": "Alice",
        "amount": 15000,
        "date_iso": "2026-08-10",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_run_publishes_you_were_caught_via_publish_checkin(fake_redis) -> None:
    """Test 4 (mandatory): _run вызывает publish_checkin с event_type='caught'.

    Verifies:
    - XADD в user-stream (sse:user:{u}:{h}).
    - inner event field = 'you_were_caught'.
    - payload содержит catcher_first_name из backend.
    - idempotency_key использует event_type='caught' (COLLISION-изоляция от checkin).
    """
    from worker.services.event_publisher import EventPublisher
    from worker.tasks.publish_you_were_caught import _run

    payload = _payload()

    # Заменяем _build_production_publisher чтобы он вернул наш EventPublisher с fake_redis
    publisher = EventPublisher(fake_redis)

    with patch(
        "worker.tasks.publish_you_were_caught._build_production_publisher",
        return_value=publisher,
    ):
        with patch(
            "worker.tasks.publish_you_were_caught._fetch_violator_first_name",
            return_value="VictimName",
        ):
            result = await _run(payload)

    assert result["ok"] is True, f"Expected ok=True, got: {result}"
    assert result["skipped"] is False
    assert result["event_type"] == "caught", (
        f"event_type должен быть 'caught' для COLLISION-изоляции. "
        f"Got: {result.get('event_type')!r}"
    )
    assert result["user_id"] == 42
    assert result["membership_id"] == "11111111-2222-3333-4444-555555555555"

    # XADD в user-stream
    user_stream = publisher.stream_key(42, payload["habit_id"])
    entries = await fake_redis.xrange(user_stream)
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["event"] == "you_were_caught"
    assert fields["habit_id"] == payload["habit_id"]
    assert fields["user_id"] == "42"
    import json
    inner = json.loads(fields["payload"])
    assert inner["catcher_user_id"] == 7
    assert inner["catcher_first_name"] == "Alice"
    assert inner["amount"] == 15000
    assert inner["violator_first_name"] == "VictimName"

    # Idempotency key: event_type='caught' namespace
    idem_key = publisher.idempotency_key(
        payload["membership_id"], payload["date_iso"], event_type="caught"
    )
    assert await fake_redis.get(idem_key) == "1", (
        f"Idempotency key должен быть установлен: {idem_key}"
    )

    # Sanity: ключ идемпотентности для 'checkin' НЕ установлен (COLLISION-изоляция).
    checkin_key = publisher.idempotency_key(
        payload["membership_id"], payload["date_iso"], event_type="checkin"
    )
    assert await fake_redis.get(checkin_key) is None, (
        f"Idempotency key для event_type='checkin' НЕ должен быть "
        f"установлен после you_were_caught (COLLISION-изоляция). "
        f"Got: {await fake_redis.get(checkin_key)!r}"
    )


@pytest.mark.asyncio
async def test_run_no_redis_configured_returns_reason() -> None:
    """Без REDIS_URL → _build_production_publisher returns None → ok=False."""
    from worker.tasks.publish_you_were_caught import _run

    old_redis = os.environ.pop("REDIS_URL", None)
    try:
        result = await _run(_payload())
    finally:
        if old_redis is not None:
            os.environ["REDIS_URL"] = old_redis

    assert result["ok"] is False
    assert result["reason"] == "no_redis_configured"


@pytest.mark.asyncio
async def test_run_second_call_skipped_on_duplicate(fake_redis) -> None:
    """Guard 2: повторный _run с тем же scope → skipped, XADD не выполняется."""
    from worker.services.event_publisher import EventPublisher
    from worker.tasks.publish_you_were_caught import _run

    payload = _payload()

    publisher = EventPublisher(fake_redis)

    with patch(
        "worker.tasks.publish_you_were_caught._build_production_publisher",
        return_value=publisher,
    ):
        with patch(
            "worker.tasks.publish_you_were_caught._fetch_violator_first_name",
            return_value="VictimName",
        ):
            r1 = await _run(payload)
            r2 = await _run(payload)

    assert r1["ok"] is True
    assert r2["ok"] is False
    assert r2["skipped"] is True

    user_stream = publisher.stream_key(42, payload["habit_id"])
    entries = await fake_redis.xrange(user_stream)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_celery_task_is_registered() -> None:
    """Sanity: декоратор celery_app.task применяется."""
    from worker.celery_app import celery_app

    assert "worker.tasks.publish_you_were_caught.run" in celery_app.tasks
