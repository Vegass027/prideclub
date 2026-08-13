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
    """At-most-once: XADD упал → return False, исключение НЕ пробрасывается.

    Покрывает стадию 2 в `publish_checkin`: SET NX успел, XADD упал.
    Идемпотентность-ключ остался в Redis с TTL 24ч.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
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

    # SET NX всё-таки успел до XADD — ключ лежит с TTL.
    assert await redis.get(publisher.idempotency_key("m-5", "2026-08-04")) == "1"


@pytest.mark.asyncio
async def test_publish_set_failure_returns_false(monkeypatch) -> None:
    """At-most-once: SET NX упал → return False, исключение НЕ пробрасывается.

    Покрывает стадию 1 в `publish_checkin`: Redis недоступен ДО XADD
    (например, сетевой блип в момент вызова). Идемпотентность-ключ НЕ
    выставлен — Guard 1 в process_checkin при Celery retry корректно
    отработает через CheckinAlreadyExistsError. Но сам факт: исключение
    НЕ должно проброситься в основной task, который уже закоммитил чек-ин.
    """
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    publisher = EventPublisher(redis)

    async def _boom(*_args, **_kwargs):
        raise ConnectionError("redis unavailable on SET")

    monkeypatch.setattr(redis, "set", _boom)

    result = await publisher.publish_checkin(
        user_id=1006,
        habit_id="h-6",
        membership_id="m-6",
        date_iso="2026-08-04",
        event=CheckinEvent(event="checkin.accepted", payload={"ok": True}),
    )
    assert result is False

    # SET упал — ключа быть не должно.
    assert await redis.get(publisher.idempotency_key("m-6", "2026-08-04")) is None
    # И в стриме ничего.
    entries = await redis.xrange(publisher.stream_key(1006, "h-6"))
    assert entries == []


@pytest.mark.asyncio
async def test_publish_stream_key_format() -> None:
    """Sanity: ключи стрима и идемпотентности в каноническом формате."""
    assert EventPublisher.stream_key(12345, "abc-uuid") == "sse:user:12345:abc-uuid"
    assert (
        EventPublisher.idempotency_key("m-uuid", "2026-08-04")
        == "sse_published:checkin:m-uuid:2026-08-04"
    )


# ============================================================================
# Pravki-bug-fixes §Z-21 (Item 6): event_type kwarg + habit_idempotency_key +
# publish_to_habit. Все тесты после строки sanity (чтобы не ломать
# нумерацию) — добавляются В КОНЕЦ файла.
# ============================================================================


@pytest.mark.asyncio
async def test_publish_checkin_event_type_kwarg_default_is_byte_for_byte_compatible(
    publisher, fake_redis
) -> None:
    """Backward-compat: без event_type kwarg ключ = старый формат
    sse_published:checkin:{m}:{d} (для существующих call-сайтов)."""
    event = CheckinEvent(event="checkin.accepted", payload={"x": 1})

    # Дефолт — без event_type (как process_checkin.py сейчас вызывает)
    await publisher.publish_checkin(
        user_id=100,
        habit_id="h-100",
        membership_id="m-100",
        date_iso="2026-08-10",
        event=event,
    )

    # Ключ должен быть В ТОЧНОСТИ старого формата, byte-for-byte.
    expected_key = "sse_published:checkin:m-100:2026-08-10"
    assert await fake_redis.get(expected_key) == "1"
    assert publisher.idempotency_key("m-100", "2026-08-10") == expected_key


@pytest.mark.asyncio
async def test_publish_checkin_event_type_caught_does_not_collide_with_checkin(
    publisher, fake_redis
) -> None:
    """COLLISION-фикс: caught-публикация для той же (m, d) НЕ пересекается
    с checkin-публикацией. Утренний checkin.rejected забивал старый ключ
    на 24ч → вечерний you_were_caught для той же (m, d) терялся.
    С новым kwarg: caught → sse_published:caught:{m}:{d} — независимо."""
    event1 = CheckinEvent(event="checkin.rejected", payload={"reason": "joined_late"})
    event2 = CheckinEvent(event="you_were_caught", payload={"catcher": "X"})

    # Утро: checkin.rejected с дефолтом (event_type="checkin")
    await publisher.publish_checkin(
        user_id=200,
        habit_id="h-200",
        membership_id="m-200",
        date_iso="2026-08-10",
        event=event1,
    )

    # Вечер: you_were_caught с явным event_type="caught"
    result_caught = await publisher.publish_checkin(
        user_id=200,
        habit_id="h-200",
        membership_id="m-200",
        date_iso="2026-08-10",
        event=event2,
        event_type="caught",
    )

    # ОБЕ публикации выполнены — нет коллизии.
    assert result_caught is True

    # Оба ключа живут независимо.
    assert await fake_redis.get("sse_published:checkin:m-200:2026-08-10") == "1"
    assert await fake_redis.get("sse_published:caught:m-200:2026-08-10") == "1"

    # И ОБА стрим-entry лежат в user-stream (один и тот же ключ стрима!).
    user_stream = publisher.stream_key(200, "h-200")
    entries = await fake_redis.xrange(user_stream)
    assert len(entries) == 2
    events = [fields["event"] for _id, fields in entries]
    assert "checkin.rejected" in events
    assert "you_were_caught" in events


# --- publish_to_habit (Item 6) ---


@pytest.mark.asyncio
async def test_publish_to_habit_first_time_xadds_habit_stream(
    publisher, fake_redis
) -> None:
    """Первый вызов publish_to_habit → XADD в sse:habit:{habit_id}."""
    habit_id = "h-broadcast-1"
    payload = {
        "event": "catch",
        "habit_id": habit_id,
        "catcher_user_id": 501,
        "violator_user_id": 502,
        "violator_membership_id": "m-502",
        "amount": 10000,
        "penalty_id": "penalty-uuid-1",
    }

    result = await publisher.publish_to_habit(
        habit_id=habit_id,
        event_type="habit_catch",
        payload=payload,
        scope_suffix="penalty-uuid-1",
    )

    assert result is True

    # Idempotency-ключ живёт 24ч.
    idem_key = publisher.habit_idempotency_key(
        event_type="habit_catch", unique_id="penalty-uuid-1",
    )
    assert await fake_redis.get(idem_key) == "1"

    # Stream entry в habit-stream.
    habit_stream = publisher.habit_stream_key(habit_id)
    entries = await fake_redis.xrange(habit_stream)
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["habit_id"] == habit_id
    assert fields["event"] == "catch"
    inner_payload = json.loads(fields["payload"])
    assert inner_payload["amount"] == 10000
    assert inner_payload["penalty_id"] == "penalty-uuid-1"


@pytest.mark.asyncio
async def test_publish_to_habit_second_time_skipped_no_xadd(
    publisher, fake_redis
) -> None:
    """Guard 2 для publish_to_habit: повторный вызов с тем же
    scope_suffix → XADD не выполнен (Celery retry / duplicate delivery)."""
    habit_id = "h-broadcast-2"
    payload = {"event": "catch", "habit_id": habit_id, "amount": 100}
    scope = "penalty-uuid-2"

    r1 = await publisher.publish_to_habit(
        habit_id=habit_id, event_type="habit_catch",
        payload=payload, scope_suffix=scope,
    )
    r2 = await publisher.publish_to_habit(
        habit_id=habit_id, event_type="habit_catch",
        payload=payload, scope_suffix=scope,
    )

    assert r1 is True
    assert r2 is False  # duplicate → skip

    habit_stream = publisher.habit_stream_key(habit_id)
    entries = await fake_redis.xrange(habit_stream)
    assert len(entries) == 1  # только первый


@pytest.mark.asyncio
async def test_publish_to_habit_namespace_isolation(
    publisher, fake_redis
) -> None:
    """Два habit-публикации с разным event_type — независимые namespace'ы."""
    habit_id = "h-namespace"
    p1 = {"event": "catch", "amount": 100}
    p2 = {"event": "leaderboard_update", "winner": "u-1"}

    # Один и тот же scope_suffix, разные event_types — должны жить отдельно.
    r1 = await publisher.publish_to_habit(
        habit_id=habit_id, event_type="habit_catch",
        payload=p1, scope_suffix="penalty-X",
    )
    r2 = await publisher.publish_to_habit(
        habit_id=habit_id, event_type="habit_leaderboard",
        payload=p2, scope_suffix="penalty-X",  # ОДИН scope_suffix
    )

    assert r1 is True
    assert r2 is True  # не duplicate, потому что разный event_type

    # Два ключа с разными event_type.
    assert await fake_redis.get("sse_published:habit_catch:penalty-X") == "1"
    assert await fake_redis.get("sse_published:habit_leaderboard:penalty-X") == "1"

    # Два event'а в habit-stream.
    entries = await fake_redis.xrange(publisher.habit_stream_key(habit_id))
    assert len(entries) == 2
    events = [fields["event"] for _id, fields in entries]
    assert events == ["catch", "leaderboard_update"]


@pytest.mark.asyncio
async def test_publish_to_habit_user_stream_not_touched(publisher, fake_redis) -> None:
    """publish_to_habit пишет ТОЛЬКО в habit-stream, не трогает user-stream.

    Это критично для Item 7 multiplex: user-stream содержит персональные
    события (checkin.accepted, you_were_caught), habit-stream — broadcast.
    Смешение сломает клиентский маппинг событий."""
    habit_id = "h-stream-isolation"
    user_id = 100
    payload = {"event": "catch", "habit_id": habit_id}

    await publisher.publish_to_habit(
        habit_id=habit_id, event_type="habit_catch",
        payload=payload, scope_suffix="penalty-Z",
    )

    # Habit-stream — есть entry.
    habit_stream = publisher.habit_stream_key(habit_id)
    assert len(await fake_redis.xrange(habit_stream)) == 1

    # User-stream — ПУСТОЙ (publish_to_habit его не трогает).
    user_stream = publisher.stream_key(user_id, habit_id)
    assert await fake_redis.xrange(user_stream) == []


@pytest.mark.asyncio
async def test_publish_to_habit_redis_unavailable_returns_false(
    publisher, fake_redis, monkeypatch
) -> None:
    """Redis down → return False (at-most-once), НЕ raise exception."""
    from redis.asyncio import Redis

    class _BoomRedis(Redis):
        async def set(self, *args, **kwargs):
            raise ConnectionError("redis unavailable on SET")

        async def xadd(self, *args, **kwargs):
            raise ConnectionError("redis unavailable on XADD")

    monkeypatch.setattr(publisher, "_redis", _BoomRedis())

    result = await publisher.publish_to_habit(
        habit_id="h-fail", event_type="habit_catch",
        payload={"event": "catch"}, scope_suffix="penalty-fail",
    )
    assert result is False
    # Стрим не тронут (XADD не выполнился).
    assert await fake_redis.xrange(publisher.habit_stream_key("h-fail")) == []


def test_habit_idempotency_key_format() -> None:
    """Sanity: ключи habit-stream + habit-idempotency в каноническом формате."""
    assert EventPublisher.habit_stream_key("h-uuid") == "sse:habit:h-uuid"
    assert (
        EventPublisher.habit_idempotency_key(
            event_type="habit_catch", unique_id="penalty-id"
        )
        == "sse_published:habit_catch:penalty-id"
    )
    # Backward-compat: idempotency_key без event_type kwarg → старый формат
    assert (
        EventPublisher.idempotency_key("m-1", "2026-08-10")
        == "sse_published:checkin:m-1:2026-08-10"
    )
    # С event_type="caught" → новый namespace
    assert (
        EventPublisher.idempotency_key("m-1", "2026-08-10", event_type="caught")
        == "sse_published:caught:m-1:2026-08-10"
    )
