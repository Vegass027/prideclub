from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.v1.users import ServiceCallerDep
from app.core.constants import CheckinRejectCode
from app.core.deps import SessionDep
from app.core.logging import get_logger
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.celery_producer import send_task

router = APIRouter()


def checkin_topic_thread_id_mismatch(habit: Any, message_thread_id: int | None) -> bool:
    """True если сообщение пришло не из ожидаемого топика.

    legacy-режим (habit.checkin_topic_thread_id IS NULL): НЕ считается
    ошибкой — топиков нет, любой message_thread_id (включая None для
    General) принимается. Это для клубов, созданных до миграции 010
    (habit_topics).
    """
    expected = getattr(habit, "checkin_topic_thread_id", None)
    if expected is None:
        return False
    return message_thread_id != expected


class CheckinEnqueueRequest(BaseModel):
    user_id: int
    chat_id: int
    message_thread_id: int | None = None
    proof_type: str
    message_id: int
    message_sent_at: datetime
    text: str | None = None
    duration_seconds: int | None = None
    # Pravki §Z-22 (Step 4, hole #4): mark for forwarded messages.
    # forwarding_signature приходит только из Telegram update (aiogram Message),
    # и backend получает его как payload.is_forwarded (bool). Worker тоже
    # валидирует через proof_validator (forward_date != None), но это defense
    # in depth — основная проверка в bot prefilter.
    is_forwarded: bool = False


class CheckinEnqueueResponse(BaseModel):
    ok: bool
    task_id: str | None = None
    code: str | None = None
    # Pravki §Z-22 (hole #1): window_start/window_end для бота, который
    # получил reject от backend defense-in-depth (а не через race-fallback
    # worker'а). Поля опциональные — бот их маппит в REJECT_OUT_OF_WINDOW
    # если есть, иначе fallback на '?'.
    window_start: str | None = None
    window_end: str | None = None


@router.post("/checkins/process", response_model=CheckinEnqueueResponse)
async def enqueue_checkin(
    payload: CheckinEnqueueRequest,
    session: SessionDep,
    _: ServiceCallerDep,
) -> CheckinEnqueueResponse:
    """Internal endpoint: бот → backend → Celery worker.

    Pravki §Z-22 (hole #1): добавлена SYNCHRONOUS defense-in-depth для
    checkin_window_closed. Бот pre-filter уже должен ловить окно сам
    (state.is_within_checkin_window, см. internal_bot.py), но если бот
    bypassed / старая версия / прямой вызов — backend режет синхронно,
    чтобы бот не успел ответить "Принято" и юзер не ждал ложно.

    Worker-таска тоже проверяет окно (race-fallback для оставшихся
    случаев), но это уже не основной путь.

    Возвращаем быстрый ack, чтобы бот не таймаутил на пиках (07:00 утра).

    Auth: X-Service-Token (уже проверен middleware).
    """
    log = get_logger("checkin_enqueue")

    habit = await HabitRepository(session).get_by_chat_id(payload.chat_id)
    if habit is None:
        return CheckinEnqueueResponse(ok=False, code=CheckinRejectCode.HABIT_NOT_FOUND.value)

    # Pravki §Z-22 (Step 3, hole #3) — позиция #2/#3/#4 в canonical order v2.
    # Membership lookup идёт РАНЬШЕ window/topic, потому что membership
    # state — structural (юзер вообще в клубе?). Если membership отсутствует
    # или paused/left — никакие window/topic checks не имеют смысла.
    #
    # NB: caught_today=True AND status=paused ВОЗМОЖЕН (см. precheck Шага 3).
    # Здесь эта пара не фильтруется — caught_today обрабатывается в worker's
    # process_checkin (race-fallback). Bot prefilter ловит caught_today РАНЬШЕ
    # paused (canonical #3 выше #6), так что в практике юзер не доходит до
    # paused-проверки если его поймали.
    membership_repo = MembershipRepository(session)
    membership = await membership_repo.get_for_user_in_habit(
        user_id=payload.user_id, habit_id=habit.id
    )
    if membership is None:
        log.info(
            "checkin_enqueue_membership_not_found",
            extra={
                "user_id": payload.user_id,
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
            },
        )
        return CheckinEnqueueResponse(ok=False, code=CheckinRejectCode.MEMBERSHIP_NOT_FOUND.value)
    if membership.status.value == "paused":
        log.info(
            "checkin_enqueue_membership_paused",
            extra={
                "user_id": payload.user_id,
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
            },
        )
        return CheckinEnqueueResponse(ok=False, code=CheckinRejectCode.MEMBERSHIP_PAUSED.value)
    if membership.status.value == "left":
        log.info(
            "checkin_enqueue_membership_left",
            extra={
                "user_id": payload.user_id,
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
            },
        )
        return CheckinEnqueueResponse(ok=False, code=CheckinRejectCode.MEMBERSHIP_LEFT.value)

    # Pravki §Z-22 (hole #1) — позиция #8 в canonical order v2 (state-of-day
    # #3-5 идут ПЕРЕД time/location; см. CheckinRejectCode docstring).
    if not habit.is_within_checkin_window(datetime.now(tz=UTC)):
        log.info(
            "checkin_enqueue_window_closed",
            extra={
                "user_id": payload.user_id,
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
            },
        )
        return CheckinEnqueueResponse(
            ok=False,
            code=CheckinRejectCode.WINDOW_CLOSED.value,
            window_start=habit.checkin_window_start.strftime("%H:%M"),
            window_end=habit.checkin_window_end.strftime("%H:%M"),
        )

    # Pravki §Z-22 (hole #2) — позиция #9 в canonical order v2.
    # checkin_topic_thread_id проверяется только если он задан (Topic-scoped
    # клубы). Если habit.checkin_topic_thread_id IS NULL — клуб работает в
    # legacy-режиме (без топиков), любой message_thread_id принимается.
    if checkin_topic_thread_id_mismatch(habit, payload.message_thread_id):
        log.info(
            "checkin_enqueue_wrong_topic",
            extra={
                "user_id": payload.user_id,
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
                "expected": habit.checkin_topic_thread_id,
                "got": payload.message_thread_id,
            },
        )
        return CheckinEnqueueResponse(ok=False, code=CheckinRejectCode.WRONG_TOPIC.value)

    # Pravki §Z-22 (Step 4, hole #4) — позиция #10 в canonical order v2.
    # Пересланные сообщения (forward_date != None в aiogram Message) не
    # принимаются — защита от cheat'а (юзер может просто переслать чужое
    # видео-кружок). Bot prefilter уже должен ловить это (на стороне бота
    # есть message.forward_date), но defense-in-depth здесь — для bypassed
    # bot / прямого вызова / race.
    if payload.is_forwarded:
        log.info(
            "checkin_enqueue_forwarded",
            extra={
                "user_id": payload.user_id,
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
            },
        )
        return CheckinEnqueueResponse(ok=False, code=CheckinRejectCode.FORWARDED.value)

    task_id = send_task(
        "checkin",
        {
            "user_id": payload.user_id,
            "habit_id": str(habit.id),
            "chat_id": payload.chat_id,
            "message_thread_id": payload.message_thread_id,
            "proof_type": payload.proof_type,
            "message_id": payload.message_id,
            "message_sent_at": payload.message_sent_at.isoformat(),
            "text": payload.text,
            "duration_seconds": payload.duration_seconds,
            "is_forwarded": payload.is_forwarded,
        },
    )

    log.info(
        "checkin_enqueued",
        extra={
            "task_id": task_id,
            "user_id": payload.user_id,
            "habit_id": str(habit.id),
            "message_id": payload.message_id,
        },
    )
    return CheckinEnqueueResponse(ok=True, task_id=task_id)