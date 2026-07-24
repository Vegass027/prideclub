"""User-facing payment endpoint (MVP).

Только mock-пополнение депозита через TelegramUserDep (initData).
Реальная интеграция с Telegram Payments будет через
`/internal/payments/confirm` (webhook от бота, см. internal_payments.py).

UI скрывает кнопку "+ Пополнить" если юзер не состоит ни в одном клубе,
но endpoint всё равно принимает любой habit_id — на MVP-мок это терпимо,
PaymentService.create membership под капотом (см. PaymentService._apply).
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.users import TelegramUserDbDep
from app.core.deps import SessionDep
from app.services.payment_service import PaymentService

router = APIRouter()


class TopupRequest(BaseModel):
    habit_id: str
    amount_kopecks: int = Field(gt=0, le=10_000_000)


class TopupResponse(BaseModel):
    ok: bool
    transaction_id: str | None = None
    new_deposit_balance: int | None = None
    code: str | None = None


@router.post("/payments/topup", response_model=TopupResponse)
async def topup_deposit(
    payload: TopupRequest,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> TopupResponse:
    """Mock-пополнение депозита (MVP).

    Идемпотентность: charge_id = "mock:{uuid4()}" — каждый вызов уникальный,
    повторный POST создаст ещё одну транзакцию (это OK для мока).
    При подключении реального провайдера charge_id станет
    telegram_payment_charge_id, и UNIQUE-индекс на transactions.idempotency_key
    обеспечит идемпотентность webhook'ов.

    Безопасность:
    - TelegramUserDbDep: initData проверен middleware; user.id — авторитетный.
    - amount_kopecks: gt=0 (нельзя пополнить на 0 или минус), le=10M (cap 100k ₽).
    - PaymentService: SELECT FOR UPDATE на membership, нет race condition.
    """
    service = PaymentService(session)
    try:
        tx = await service.confirm_deposit_topup(
            charge_id=f"mock:{uuid4()}",
            user_id=user.id,
            habit_id=payload.habit_id,
            amount_kopecks=payload.amount_kopecks,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 — payment_failed — единый ответ UI
        await session.rollback()
        return TopupResponse(ok=False, code="payment_failed")

    return TopupResponse(
        ok=True,
        transaction_id=str(tx.id),
        new_deposit_balance=tx.balance_after,
    )
