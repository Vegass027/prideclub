"""Тесты для worker-таски process_checkin.

Покрывает:
- happy path: чек-ин проходит, возвращается checkin_id;
- идемпотентность: повторный вызов → duplicate=True, без дубля в БД;
- proof validation error → откат, ok=False с кодом;
- window closed → откат, ok=False с кодом;
- membership not active → откат, ok=False с кодом.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest

from app.core.constants import CheckinStatus, MembershipStatus


# cache=None — по умолчанию в _process(). Тесты не поднимают Redis, что
# соответствует правильному DI-паттерну из AGENTS.md.


@pytest.mark.asyncio
async def test_process_checkin_happy_path(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    os.environ["REDIS_URL"] = "redis://localhost:6379/0"

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1001)
        # Окно 00:00-23:59 чтобы тест был time-stable (дефолт 7-10 в MSK,
        # но тест-раннер может выполняться в любой момент UTC).
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        membership = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await session.commit()

    payload = {
        "user_id": 1001,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100500,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is True
    assert "checkin_id" in result
    assert result["created"] is True

    async with worker_db.session_factory() as session:
        from sqlalchemy import select

        from app.models.checkin import Checkin

        c = (
            await session.execute(
                select(Checkin).where(Checkin.membership_id == membership.id)
            )
        ).scalar_one()
        assert c.status == CheckinStatus.DONE
        assert c.proof_message_id == 100500


@pytest.mark.asyncio
async def test_process_checkin_duplicate_idempotent(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1002)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        membership = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await worker_db.add_checkin(
            session, membership_id=membership.id, on_date=date.today()
        )
        await session.commit()

    payload = {
        "user_id": 1002,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100501,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is True
    assert result.get("duplicate") is True

    async with worker_db.session_factory() as session:
        from sqlalchemy import func, select

        from app.models.checkin import Checkin

        count = (
            await session.execute(
                select(func.count())
                .select_from(Checkin)
                .where(Checkin.membership_id == membership.id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_process_checkin_window_closed(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1003)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=7,
            checkin_window_end_hour=10,
        )
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    payload = {
        "user_id": 1003,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100502,
        # "Сейчас" в UTC — окно 07-10 MSK (= 04-07 UTC) закрыто в любое другое время UTC.
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is False
    assert result.get("code") == "checkin_window_closed"


@pytest.mark.asyncio
async def test_process_checkin_wrong_topic(worker_db) -> None:
    """Сообщение пришло не из топика чек-инов → CheckinWrongTopicError → code='not_checkin_topic'.

    Регрессия: до фикса тут падал NameError на CheckinWrongTopicError,
    таска уходила в retry-цикл и чек-ин не записывался.
    """
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1003)
        habit = await worker_db.add_habit(
            session,
            checkin_topic_thread_id=42,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    payload = {
        "user_id": 1003,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100503,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
        "message_thread_id": 99,  # не 42
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is False
    assert result.get("code") == "not_checkin_topic"


@pytest.mark.asyncio
async def test_process_checkin_correct_topic_accepted(worker_db) -> None:
    """Положительный случай топик-фильтра: message_thread_id совпадает → запись."""
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1004)
        habit = await worker_db.add_habit(
            session,
            checkin_topic_thread_id=42,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    payload = {
        "user_id": 1004,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100504,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
        "message_thread_id": 42,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is True
    assert result["created"] is True


@pytest.mark.asyncio
async def test_process_checkin_wrong_proof_type(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1004)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )  # VIDEO_NOTE по умолчанию
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    payload = {
        "user_id": 1004,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "photo",  # не соответствует VIDEO_NOTE
        "message_id": 100503,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is False
    assert result.get("code") == "wrong_type"


@pytest.mark.asyncio
async def test_process_checkin_membership_inactive(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1005)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        await worker_db.add_membership(
            session,
            user_id=user.id,
            habit_id=habit.id,
            status=MembershipStatus.PAUSED,
        )
        await session.commit()

    payload = {
        "user_id": 1005,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100504,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is False
    assert result.get("code") == "membership_not_active"


@pytest.mark.asyncio
async def test_process_checkin_membership_not_found(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        await worker_db.add_user(session, id=1006)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        await session.commit()

    payload = {
        "user_id": 1006,  # нет membership для этого user_id
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 100505,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is False
    assert result.get("code") == "membership_not_found"


# === Шаг 3 плана sse+redis.md: SSE-публикация ============================


class _RecordingPublisher:
    """Duck-typed publisher, который пишет все вызовы в список.

    Worker-тесты не поднимают реальный Redis — нет смысла использовать
    fakeredis здесь, когда тестируем сам факт «вызвали / не вызвали».
    Контракт: ровно те же сигнатуры, что у ``EventPublisher.publish_checkin``.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish_checkin(
        self,
        *,
        user_id: int,
        habit_id: str,
        membership_id: str,
        date_iso: str,
        event,
    ) -> bool:
        self.calls.append(
            {
                "user_id": user_id,
                "habit_id": habit_id,
                "membership_id": membership_id,
                "date_iso": date_iso,
                "event_type": event.event,
                "payload": event.payload,
            }
        )
        return True


@pytest.mark.asyncio
async def test_process_checkin_duplicate_skips_publisher(worker_db) -> None:
    """Guard 1: уже есть Checkin за сегодня → publisher НЕ вызывается.

    Защита от Celery redelivery + двух видео-кружков подряд. UI уже
    показывает done, событие бесполезно.
    """
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=2001)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        membership = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await worker_db.add_checkin(
            session, membership_id=membership.id, on_date=date.today()
        )
        await session.commit()

    publisher = _RecordingPublisher()
    payload = {
        "user_id": 2001,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200501,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(
        payload,
        session_factory=worker_db.session_factory,
        publisher=publisher,
    )

    assert result["ok"] is True
    assert result["duplicate"] is True
    assert publisher.calls == []  # Guard 1 — никаких Redis-операций


@pytest.mark.asyncio
async def test_process_checkin_happy_path_publishes_accepted(worker_db) -> None:
    """Happy path: created=True → publisher.publish_checkin вызван с
    CheckinEvent(event='checkin.accepted', payload=TodayResponse-like dict)."""
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=2002)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        membership = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        await session.commit()

    publisher = _RecordingPublisher()
    payload = {
        "user_id": 2002,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200502,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(
        payload,
        session_factory=worker_db.session_factory,
        publisher=publisher,
    )

    assert result["ok"] is True
    assert result["created"] is True
    assert len(publisher.calls) == 1

    call = publisher.calls[0]
    assert call["user_id"] == 2002
    assert call["habit_id"] == habit.id
    assert call["membership_id"] == membership.id
    assert call["event_type"] == "checkin.accepted"
    # payload — полный TodayResponse с ключами habit/membership/checkin.
    assert "habit" in call["payload"]
    assert "membership" in call["payload"]
    assert "checkin" in call["payload"]
    assert call["payload"]["checkin"]["status"] == "done"


@pytest.mark.asyncio
async def test_process_checkin_rejected_publishes_rejected_with_reason(
    worker_db,
) -> None:
    """ok=False + reason != already_exists → publisher вызван с
    checkin.rejected + reason из exc.code."""
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=2003)
        # Окно 7-10 MSK → сейчас UTC вне его.
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=7,
            checkin_window_end_hour=10,
        )
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    publisher = _RecordingPublisher()
    payload = {
        "user_id": 2003,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200503,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(
        payload,
        session_factory=worker_db.session_factory,
        publisher=publisher,
    )

    assert result["ok"] is False
    assert result["code"] == "checkin_window_closed"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["event_type"] == "checkin.rejected"
    assert publisher.calls[0]["payload"]["reason"] == "checkin_window_closed"
    assert publisher.calls[0]["payload"]["habit_id"] == habit.id


@pytest.mark.asyncio
async def test_process_checkin_no_publisher_means_no_publish(worker_db) -> None:
    """publisher=None (default) → задача работает как раньше, без Redis-операций.

    Регрессия: после добавления publisher-параметра в _process по
    умолчанию он Optional, и существующие вызовы (например, тесты
    выше) не должны сломаться.
    """
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=2004)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    payload = {
        "user_id": 2004,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200504,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    # publisher не передаём — должно просто работать.
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is True
    assert result["created"] is True


@pytest.mark.asyncio
async def test_process_checkin_membership_not_found_skips_publish(worker_db) -> None:
    """MembershipNotFound → publisher НЕ вызывается (нет membership_id для идемпотентности)."""
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        await worker_db.add_user(session, id=2005)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        await session.commit()

    publisher = _RecordingPublisher()
    payload = {
        "user_id": 2005,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200505,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }
    result = await _process(
        payload,
        session_factory=worker_db.session_factory,
        publisher=publisher,
    )

    assert result["ok"] is False
    assert result["code"] == "membership_not_found"
    # Внутри _publish_checkin_rejected ранний return при None membership.
    assert publisher.calls == []


class _ExplodingSetPublisher:
    """Зарезервировано под регрессию для случая, когда какой-то будущий
    publisher забудет try/except внутри publish_checkin. Сейчас
    EventPublisher ловит оба фейла (set + xadd), поэтому
    _process никогда не видит исключение наружу — этот сценарий
    покрыт парой тестов в test_event_publisher.py:
        - test_publish_set_failure_returns_false
        - test_publish_xadd_failure_returns_false
    """


@pytest.mark.asyncio
async def test_process_checkin_redis_outage_at_publish_survives(
    worker_db, monkeypatch
) -> None:
    """Интеграционный: Redis умер МЕЖДУ commit и publish → task не падает.

    Используем настоящий ``EventPublisher`` с fakeredis, но патчим
    ``fakeredis.set`` так, чтобы он бросал ``ConnectionError``. Это
    имитирует сетевой блип в production Redis сразу после успешного
    commit'а чек-ина. Инвариант Step 3: publish — best-effort, чек-ин
    в БД остаётся, _process возвращает успешный результат.
    """
    import fakeredis.aioredis

    from worker.services.event_publisher import EventPublisher
    from worker.tasks.process_checkin import _process

    membership_id_holder: dict[str, str] = {}
    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=2006)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        membership = await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
        membership_id_holder["id"] = membership.id
        await session.commit()

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _boom(*_args, **_kwargs):
        raise ConnectionError("simulated redis outage at SET NX")

    monkeypatch.setattr(redis, "set", _boom)
    publisher = EventPublisher(redis)

    payload = {
        "user_id": 2006,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200506,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }

    result = await _process(
        payload,
        session_factory=worker_db.session_factory,
        publisher=publisher,
    )

    # ok=True несмотря на то, что публикация упала — это и есть инвариант.
    assert result["ok"] is True
    assert result["created"] is True

    # Чек-ин реально в БД — коммит прошёл, publisher-фейл его не откатил.
    async with worker_db.session_factory() as session:
        from sqlalchemy import func, select

        from app.models.checkin import Checkin

        count = (
            await session.execute(
                select(func.count())
                .select_from(Checkin)
                .where(Checkin.membership_id == membership_id_holder["id"])
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_process_checkin_rejected_redis_outage_survives(
    worker_db, monkeypatch
) -> None:
    """Интеграционный: rejected-путь + Redis outage → task возвращает rejected, не падает."""
    import fakeredis.aioredis

    from worker.services.event_publisher import EventPublisher
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=2007)
        # Окно 7-10 MSK → за пределами текущего UTC.
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=7,
            checkin_window_end_hour=10,
        )
        await worker_db.add_membership(session, user_id=user.id, habit_id=habit.id)
        await session.commit()

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async def _boom(*_args, **_kwargs):
        raise ConnectionError("simulated redis outage at SET NX")

    monkeypatch.setattr(redis, "set", _boom)
    publisher = EventPublisher(redis)

    payload = {
        "user_id": 2007,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 200507,
        "message_sent_at": datetime.now(tz=timezone.utc).isoformat(),
        "duration_seconds": 5,
    }

    result = await _process(
        payload,
        session_factory=worker_db.session_factory,
        publisher=publisher,
    )

    # rejected-результат вернулся, исключение НЕ пробросилось.
    assert result["ok"] is False
    assert result["code"] == "checkin_window_closed"


# ---------------------------------------------------------------------------
# Pravki-bug-fixes §Z-19: worker race-fallback для joined_late.
#
# Сценарий: бот pre-filter пропустил (старый бот, race, прямой вызов), и
# worker всё равно отвергает joined_late. Проверяем что:
# 1. result содержит code="joined_late" + window_start/end (race-fallback для бота).
# 2. НЕ создаются Checkin/Penalty/Transaction (DB integrity).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_checkin_joined_late_returns_window_fallback(worker_db) -> None:
    """Worker отвергает joined_late + возвращает window_start/end для race-fallback бота.

    Это симметричная серверная защита от race / старого бота / прямого вызова
    (Pravki-bug-fixes §Z-19). Даже если бот pre-filter был bypassed (или
    юзер вступил между pre-filter и worker'ом), worker повторно блокирует
    чек-ин и возвращает race-fallback данные для дружественного сообщения.
    """
    from datetime import datetime, timezone as tz
    from zoneinfo import ZoneInfo
    from worker.tasks.process_checkin import _process

    MSK = ZoneInfo("Europe/Moscow")
    # joined_at = 16:00 MSK сегодня (= 13:00 UTC) — после окна 06-12.
    # Выбираем фиксированный час а не datetime.now() чтобы тест был
    # time-stable (joined_late должен срабатывать даже если тест запускается
    # в момент, когда окно открыто).
    today_utc = datetime.now(tz=tz.utc)
    joined_at = today_utc.replace(hour=13, minute=0, second=0, microsecond=0)

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1501)
        # Окно 06:00-12:00 MSK. joined_at = 16:00 MSK → после окна.
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=6,
            checkin_window_end_hour=12,
        )
        await worker_db.add_membership(
            session,
            user_id=user.id,
            habit_id=habit.id,
            joined_at=joined_at,
        )
        await session.commit()

    payload = {
        "user_id": 1501,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 150101,
        "message_sent_at": datetime.now(tz=tz.utc).isoformat(),
        "duration_seconds": 5,
        "message_thread_id": None,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)

    # Главное: race-fallback для бота.
    assert result["ok"] is False, f"Expected ok=False, got: {result}"
    assert result["code"] == "joined_late", (
        f"Expected code='joined_late', got: {result.get('code')}"
    )
    # Window times для дружественного сообщения бота.
    assert result["window_start"] == "06:00", (
        f"Expected window_start='06:00', got: {result.get('window_start')}"
    )
    assert result["window_end"] == "12:00", (
        f"Expected window_end='12:00', got: {result.get('window_end')}"
    )


@pytest.mark.asyncio
async def test_process_checkin_joined_late_no_db_writes_in_worker(worker_db) -> None:
    """При worker-level joined_late отказе НЕ создаются Checkin/Penalty/Transaction.

    Симметричный тест backend Z-19.7: проверяет DB integrity и на уровне
    worker'а (где реальные INSERT'ы могли бы произойти).
    """
    from datetime import datetime, timezone as tz
    from zoneinfo import ZoneInfo
    from app.models.checkin import Checkin
    from app.models.penalty import Penalty
    from app.models.transaction import Transaction
    from sqlalchemy import select, func
    from worker.tasks.process_checkin import _process

    MSK = ZoneInfo("Europe/Moscow")
    today_utc = datetime.now(tz=tz.utc)
    joined_at = today_utc.replace(hour=13, minute=0, second=0, microsecond=0)

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1502)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=6,
            checkin_window_end_hour=12,
        )
        await worker_db.add_membership(
            session,
            user_id=user.id,
            habit_id=habit.id,
            joined_at=joined_at,
        )
        await session.commit()

    # Считаем строки ДО вызова worker'а.
    async with worker_db.session_factory() as session:
        checks_before = await session.scalar(select(func.count()).select_from(Checkin)) or 0
        penalties_before = await session.scalar(select(func.count()).select_from(Penalty)) or 0
        tx_before = await session.scalar(select(func.count()).select_from(Transaction)) or 0

    payload = {
        "user_id": 1502,
        "habit_id": habit.id,
        "chat_id": habit.chat_id,
        "proof_type": "video_note",
        "message_id": 150201,
        "message_sent_at": datetime.now(tz=tz.utc).isoformat(),
        "duration_seconds": 5,
        "message_thread_id": None,
    }
    result = await _process(payload, session_factory=worker_db.session_factory)

    assert result["ok"] is False
    assert result["code"] == "joined_late"

    # Считаем строки ПОСЛЕ. Должны быть идентичны (никто ничего не создал).
    async with worker_db.session_factory() as session:
        checks_after = await session.scalar(select(func.count()).select_from(Checkin)) or 0
        penalties_after = await session.scalar(select(func.count()).select_from(Penalty)) or 0
        tx_after = await session.scalar(select(func.count()).select_from(Transaction)) or 0

    assert checks_after == checks_before, (
        f"Checkin count changed: {checks_before} → {checks_after}. "
        f"Worker created a Checkin despite joined_late rejection!"
    )
    assert penalties_after == penalties_before, (
        f"Penalty count changed: {penalties_before} → {penalties_after}"
    )
    assert tx_after == tx_before, (
        f"Transaction count changed: {tx_before} → {tx_after}"
    )
