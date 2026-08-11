from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.v1.users import ServiceCallerDep
from app.core.constants import CheckinRejectCode
from app.core.deps import SessionDep
from app.core.logging import get_logger
from app.repositories.habit_repository import HabitRepository
from app.services.celery_producer import send_task

router = APIRouter()


class CheckinEnqueueRequest(BaseModel):
    user_id: int
    chat_id: int
    message_thread_id: int | None = None
    proof_type: str
    message_id: int
    message_sent_at: datetime
    text: str | None = None
    duration_seconds: int | None = None


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

    # Pravki §Z-22 (hole #1) — позиция #5 в canonical order (после #1 habit,
    # #2 membership, #3 paused, #4 left — те появятся в Шаге 3).
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