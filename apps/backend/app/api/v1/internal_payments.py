from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user_internal
from app.db.session import get_session
from app.services.payment_service import PaymentService


router = APIRouter()


class PaymentConfirmRequest(BaseModel):
    charge_id: str
    user_id: int
    habit_id: str
    amount_kopecks: int
    kind: str  # subscription | deposit_topup
    months: int = 1


class PaymentConfirmResponse(BaseModel):
    ok: bool
    transaction_id: str | None = None
    code: str | None = None


@router.post("/payments/confirm", response_model=PaymentConfirmResponse)
async def confirm_payment(
    payload: PaymentConfirmRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(current_user_internal),
) -> PaymentConfirmResponse:
    service = PaymentService(session)
    try:
        if payload.kind == "subscription":
            tx = await service.confirm_subscription(
                charge_id=payload.charge_id,
                user_id=payload.user_id,
                habit_id=payload.habit_id,
                amount_kopecks=payload.amount_kopecks,
                months=payload.months,
            )
        elif payload.kind == "deposit_topup":
            tx = await service.confirm_deposit_topup(
                charge_id=payload.charge_id,
                user_id=payload.user_id,
                habit_id=payload.habit_id,
                amount_kopecks=payload.amount_kopecks,
            )
        else:
            return PaymentConfirmResponse(ok=False, code="unknown_kind")
        await session.commit()
        return PaymentConfirmResponse(ok=True, transaction_id=str(tx.id))
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        return PaymentConfirmResponse(ok=False, code=f"internal:{type(exc).__name__}")