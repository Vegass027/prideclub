"""Unit-тесты для worker.services.event_publisher.

Шаг 3 плана sse+redis.md: проверяем Guard 2 (idempotency-ключ SET NX EX)
и базовую функциональность XADD. Никакого async-DB, только fakeredis.
"""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from worker.services.event_publisher import CheckinEvent, EventPublisher


@pytest.fixture
def fake_redis() -> fakeredis.aioredis.FakeRedis:
    """Изолированный FakeRedis для каждого теста.

    ``decode_responses=True`` критичен — без него поля XADD возвращаются
    как ``bytes``, и тесты ломаются на ``entry["payload"]``.
    """
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def publisher(fake_redis) -> EventPublisher:
    return EventPublisher(fake_redis)


@pytest.mark.asyncio
async def test_publish_first_time_xadds_entry(publisher, fake_redis) -> None:
    """Первый вызов для (membership, date) → XADD выполнен, idempotency-ключ выставлен."""
    user_id = 1001
    habit_id = "h-uuid-1"
    membership_id = "m-uuid-1"
    date_iso = "2026-08-04"
    event = CheckinEvent(
        event="checkin.accepted",
        payload={"habit_id": habit_id, "status": "done"},
    )

    result = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso=date_iso,
        event=event,
    )

    assert result is True

    # Idempotency-ключ живёт ровно 24ч.
    idem_key = publisher.idempotency_key(membership_id, date_iso)
    assert await fake_redis.get(idem_key) == "1"
    ttl = await fake_redis.ttl(idem_key)
    assert 86000 <= ttl <= 86400

    # XADD выполнен — entry в стриме.
    stream_key = publisher.stream_key(user_id, habit_id)
    entries = await fake_redis.xrange(stream_key)
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["event"] == "checkin.accepted"
    assert fields["habit_id"] == habit_id
    assert fields["user_id"] == str(user_id)
    assert "occurred_at" in fields
    payload = json.loads(fields["payload"])
    assert payload["status"] == "done"


@pytest.mark.asyncio
async def test_publish_second_time_skipped_no_xadd(publisher, fake_redis) -> None:
    """Guard 2: повторный вызов с тем же (membership, date) → XADD не выполнен."""
    user_id = 1002
    habit_id = "h-uuid-2"
    membership_id = "m-uuid-2"
    date_iso = "2026-08-04"
    event = CheckinEvent(
        event="checkin.accepted",
        payload={"status": "done"},
    )

    first = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso=date_iso,
        event=event,
    )
    assert first is True

    # Повторный вызов — это имитация Celery redelivery.
    event_two = CheckinEvent(
        event="checkin.accepted",
        payload={"status": "done", "counter": 2},
    )
    second = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso=date_iso,
        event=event_two,
    )
    assert second is False

    # XADD был один раз — в стриме только первая запись.
    stream_key = publisher.stream_key(user_id, habit_id)
    entries = await fake_redis.xrange(stream_key)
    assert len(entries) == 1
    payload = json.loads(entries[0][1]["payload"])
    assert "counter" not in payload  # вторая запись не подмешалась


@pytest.mark.asyncio
async def test_publish_different_date_xadds_again(publisher, fake_redis) -> None:
    """Разные date_iso → независимые idempotency-ключи, XADD выполняется каждый раз."""
    user_id = 1003
    habit_id = "h-uuid-3"
    membership_id = "m-uuid-3"

    ok_day1 = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso="2026-08-04",
        event=CheckinEvent(event="checkin.accepted", payload={"day": 1}),
    )
    ok_day2 = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso="2026-08-05",
        event=CheckinEvent(event="checkin.accepted", payload={"day": 2}),
    )

    assert ok_day1 is True
    assert ok_day2 is True

    stream_key = publisher.stream_key(user_id, habit_id)
    entries = await fake_redis.xrange(stream_key)
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_publish_rejected_event_payload(publisher, fake_redis) -> None:
    """checkin.rejected: payload содержит habit_id/reason/message."""
    user_id = 1004
    habit_id = "h-uuid-4"
    membership_id = "m-uuid-4"
    date_iso = "2026-08-04"

    result = await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso=date_iso,
        event=CheckinEvent(
            event="checkin.rejected",
            payload={
                "habit_id": habit_id,
                "reason": "checkin_window_closed",
                "message": "checkin_window_closed",
            },
        ),
    )
    assert result is True

    stream_key = publisher.stream_key(user_id, habit_id)
    entries = await fake_redis.xrange(stream_key)
    assert len(entries) == 1
    payload = json.loads(entries[0][1]["payload"])
    assert payload["reason"] == "checkin_window_closed"
    assert payload["message"] == "checkin_window_closed"
    assert payload["habit_id"] == habit_id


@pytest.mark.asyncio
async def test_publish_xadd_failure_returns_false(monkeypatch) -> None:
    """At-most-once: XADD упал → return False, исключение НЕ пробрасывается."""
    redis = fakeredis.aioredis.FakeRedis()
    publisher = EventPublisher(redis)

    async def _boom(*_args, **_kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(redis, "xadd", _boom)

    result = await publisher.publish_checkin(
        user_id=1005,
        habit_id="h-5",
        membership_id="m-5",
        date_iso="2026-08-04",
        event=CheckinEvent(event="checkin.accepted", payload={"ok": True}),
    )
    assert result is False


@pytest.mark.asyncio
async def test_publish_stream_key_format() -> None:
    """Sanity: ключи стрима и идемпотентности в каноническом формате."""
    assert EventPublisher.stream_key(12345, "abc-uuid") == "sse:user:12345:abc-uuid"
    assert (
        EventPublisher.idempotency_key("m-uuid", "2026-08-04")
        == "sse_published:checkin:m-uuid:2026-08-04"
    )
