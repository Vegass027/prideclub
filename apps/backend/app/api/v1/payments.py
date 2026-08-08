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
    """Pravki-deposit-sse.md §Z-2.5: депозит глобальный (на users.deposit_balance).

    `habit_id` опционален — раньше был обязательным для legacy-кода
    PaymentService._apply, который пытался создать membership при её
    отсутствии. После PR #1 membership-creation на topup больше не нужен:
    если user не имеет membership для (user_id, habit_id), транзакция
    просто записывается без `related_membership_id` (FK на memberships
    nullable). Это полностью закрывает §Z-2.5 «не принимает habit_id» —
    фронт может либо не слать поле вообще, либо слать `null`.

    Frontend PR #2 (apps/frontend/src/shared/hooks/index.ts:useTopUpDeposit)
    сейчас шлёт `{ habit_id: "", amount_kopecks }` — мы нормализуем
    пустую строку в None на уровне handler'а для backward-compat с
    таким клиентом (нормализация → None → skip membership lookup).
    """

    habit_id: str | None = None
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
    - PaymentService: SELECT FOR UPDATE на user (не membership — user-lock
      сериализует все catch/topup этого юзера в любых клубах, PR #1 §Z-2.4).
    """
    # Normalize empty-string → None (старые клиенты шлют habit_id=""; для
    # Pydantic это валидная непустая строка, но для бизнес-логики это "нет клуба").
    habit_id = payload.habit_id if payload.habit_id else None

    service = PaymentService(session)
    try:
        tx = await service.confirm_deposit_topup(
            charge_id=f"mock:{uuid4()}",
            user_id=user.id,
            habit_id=habit_id,
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
