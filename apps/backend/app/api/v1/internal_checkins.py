from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user_internal
from app.core.logging import get_logger
from app.db.session import get_session
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


@router.post("/checkins/process", response_model=CheckinEnqueueResponse)
async def enqueue_checkin(
    payload: CheckinEnqueueRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(current_user_internal),
) -> CheckinEnqueueResponse:
    """Internal endpoint: бот → backend → Celery worker.

    НИКАКОЙ валидации медиа/window здесь — только маршрутизация по chat_id → habit_id
    и постановка задачи в очередь. Валидация и запись — в worker-таске `process_checkin.run`
    (см. docs/02 §4).

    Возвращаем быстрый ack, чтобы бот не таймаутил на пиках (07:00 утра).

    Auth: X-Service-Token (уже проверен middleware).
    """
    log = get_logger("checkin_enqueue")

    habit = await HabitRepository(session).get_by_chat_id(payload.chat_id)
    if habit is None:
        return CheckinEnqueueResponse(ok=False, code="habit_not_found")

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