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
from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CheckinStatus, MembershipStatus, ProofType


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
        await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
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
async def test_process_checkin_wrong_proof_type(worker_db) -> None:
    from worker.tasks.process_checkin import _process

    async with worker_db.session_factory() as session:
        user = await worker_db.add_user(session, id=1004)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )  # VIDEO_NOTE по умолчанию
        await worker_db.add_membership(
            session, user_id=user.id, habit_id=habit.id
        )
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
        user = await worker_db.add_user(session, id=1006)
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