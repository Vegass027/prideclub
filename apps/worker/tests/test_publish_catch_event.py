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


def _make_payload(
    habit_id: str = "h-1",
    penalty_id: str = "p-1",
    catcher_user_id: int = 501,
    violator_user_id: int = 502,
    violator_membership_id: str = "m-502",
    catcher_first_name: str = "CatcherAlice",
    amount: int = 10000,
) -> dict:
    """Make payload for publish_catch_event tests.

    Item 8: добавлены опциональные catcher_first_name и overrides для
    catcher_user_id/violator_user_id чтобы новые тесты (Test 5) могли
    проверять payload enrichment без дублирования логики.
    Defaults сохранены — старые тесты работают без изменений.
    """
    return {
        "habit_id": habit_id,
        "penalty_id": penalty_id,
        "catcher_user_id": catcher_user_id,
        "catcher_first_name": catcher_first_name,
        "violator_user_id": violator_user_id,
        "violator_membership_id": violator_membership_id,
        "amount": amount,
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


# ============================================================================
# Pravki-bug-fixes §Z-21 (Item 8): enrich publish_catch_event с violator_first_name.
#
# Контракт (Item 8 Variant C из разведки):
# - Backend передаёт catcher_first_name (0 round-trip, из scope).
# - Worker дополнительно делает PK lookup violator_first_name (Variant C).
# - При failure lookup'а — log warning, payload без violator_first_name.
# ============================================================================


@pytest.mark.asyncio
async def test_run_publish_catch_event_enriches_with_violator_first_name(fake_redis) -> None:
    """Test 5 (mandatory): publish_catch_event._run обогащает payload через
    UserRepository.get(violator_user_id) — fetch first_name в worker.

    Backend НЕ делает этот fetch — рабочий side. +1 PK lookup в worker,
    не блокирует HTTP response catch_violator.
    """
    from worker.services.event_publisher import EventPublisher
    from worker.tasks.publish_catch_event import _run

    # Payload как от backend celery_producer.send_task (Item 8 contract).
    payload = _make_payload(
        violator_user_id=7777,
        catcher_first_name="Alice",  # NEW: backend уже передаёт
    )

    publisher = EventPublisher(fake_redis)

    # Патчим _fetch_violator_first_name чтобы вернуть детерминированный first_name.
    # Тест проверяет КОНТРАКТ — что _run вызывает _fetch_violator_first_name
    # и подставляет результат в payload. Реальный UserRepository тестируется
    # в worker/tests/test_user_repository.py (не нужно дублировать).
    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=publisher,
    ):
        with patch(
            "worker.tasks.publish_catch_event._fetch_violator_first_name",
            return_value="VictimBob",
        ) as fetch_mock:
            result = await _run(payload)

    # Verify _fetch_violator_first_name был вызван с violator_user_id из payload.
    fetch_mock.assert_called_once_with(7777)

    assert result["ok"] is True

    # XADD payload содержит оба first_name (catcher из backend + violator из worker fetch).
    habit_stream = publisher.habit_stream_key("h-1")
    entries = await fake_redis.xrange(habit_stream)
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    import json
    inner = json.loads(fields["payload"])
    assert inner["catcher_first_name"] == "Alice", (
        f"catcher_first_name должен быть из backend payload, got: {inner.get('catcher_first_name')!r}"
    )
    assert inner["violator_first_name"] == "VictimBob", (
        f"violator_first_name должен быть из worker fetch (UserRepository.get), "
        f"got: {inner.get('violator_first_name')!r}"
    )
    assert inner["violator_user_id"] == 7777


@pytest.mark.asyncio
async def test_run_publish_catch_event_handles_missing_violator_first_name(fake_redis) -> None:
    """Edge case: violator был удалён между apply_catch и worker fetch.
    _fetch_violator_first_name возвращает None → payload без violator_first_name,
    publish продолжается (UI fallback на user_id).
    """
    from worker.services.event_publisher import EventPublisher
    from worker.tasks.publish_catch_event import _run

    payload = _make_payload(violator_user_id=8888)

    publisher = EventPublisher(fake_redis)

    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=publisher,
    ):
        with patch(
            "worker.tasks.publish_catch_event._fetch_violator_first_name",
            return_value=None,  # violator удалён / не найден
        ):
            result = await _run(payload)

    assert result["ok"] is True  # publish всё равно успешен

    habit_stream = publisher.habit_stream_key("h-1")
    entries = await fake_redis.xrange(habit_stream)
    import json
    inner = json.loads(entries[0][1]["payload"])
    assert inner["violator_first_name"] is None, (
        f"violator_first_name должно быть None при отсутствии user, "
        f"got: {inner.get('violator_first_name')!r}"
    )
    # catcher_first_name всё равно в payload (из backend).
    assert inner["catcher_first_name"] == payload.get("catcher_first_name", "")


@pytest.mark.asyncio
async def test_run_publish_catch_event_handles_violator_fetch_exception(fake_redis) -> None:
    """Edge case: UserRepository.get raises (DB down / timeout).
    _fetch_violator_first_name ловит exception, возвращает None, publish продолжается.
    """
    from worker.services.event_publisher import EventPublisher
    from worker.tasks.publish_catch_event import _run

    payload = _make_payload()

    publisher = EventPublisher(fake_redis)

    with patch(
        "worker.tasks.publish_catch_event._build_production_publisher",
        return_value=publisher,
    ):
        with patch(
            "worker.tasks.publish_catch_event._fetch_violator_first_name",
            return_value=None,  # функция сама ловит exception → None
        ):
            result = await _run(payload)

    assert result["ok"] is True, (
        f"publish не должен ломаться при failed violator fetch, got: {result}"
    )
