from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.users import ServiceCallerDep
from app.core.deps import SessionDep
from app.core.logging import get_logger
from app.repositories.habit_repository import HabitRepository
from app.services.celery_producer import send_task

router = APIRouter()


class PaymentConfirmRequest(BaseModel):
    charge_id: str
    user_id: int
    chat_id: int
    amount_kopecks: int = Field(gt=0)
    kind: str  # subscription | deposit_topup
    months: int = 1


class PaymentConfirmResponse(BaseModel):
    ok: bool
    task_id: str | None = None
    code: str | None = None


@router.post("/payments/confirm", response_model=PaymentConfirmResponse)
async def enqueue_payment_confirm(
    payload: PaymentConfirmRequest,
    session: SessionDep,
    _: ServiceCallerDep,
) -> PaymentConfirmResponse:
    """Internal endpoint: Telegram Payments webhook → backend → Celery worker.

    НИКАКОГО списания/зачисления здесь. Идемпотентность обеспечивается в worker-таске
    `process_payment.run` через `Transaction.idempotency_key = charge_id` — это
    UNIQUE-индекс на таблице transactions, поэтому дубль charge_id не пройдёт.

    Возвращаем task_id — бот может использовать для трекинга результата через
    `celery_result_backend`.

    Auth: X-Service-Token (уже проверен middleware).
    """
    log = get_logger("payment_enqueue")

    habit = await HabitRepository(session).get_by_chat_id(payload.chat_id)
    if habit is None:
        return PaymentConfirmResponse(ok=False, code="habit_not_found")

    task_id = send_task(
        "payment",
        {
            "charge_id": payload.charge_id,
            "user_id": payload.user_id,
            "habit_id": str(habit.id),
            "amount_kopecks": payload.amount_kopecks,
            "kind": payload.kind,
            "months": payload.months,
        },
    )

    log.info(
        "payment_enqueued",
        extra={
            "task_id": task_id,
            "charge_id": payload.charge_id,
            "user_id": payload.user_id,
            "habit_id": str(habit.id),
            "kind": payload.kind,
            "amount_kopecks": payload.amount_kopecks,
        },
    )
    return PaymentConfirmResponse(ok=True, task_id=task_id)