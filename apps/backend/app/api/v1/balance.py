from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user
from app.core.security import TelegramUser
from app.db.session import get_session
from app.models.membership import Membership
from app.models.transaction import Transaction


router = APIRouter()


@router.get("/balance")
async def balance(
    user: TelegramUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    memberships = (await session.execute(
        select(Membership).where(Membership.user_id == user.id)
    )).scalars().all()
    deposit_balance = sum(m.deposit_balance for m in memberships)

    txns = (await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.created_at.desc())
        .limit(50)
    )).scalars().all()

    return {
        "deposit_balance": deposit_balance,
        "history": [
            {
                "id": str(t.id),
                "type": t.type,
                "amount": t.amount,
                "balance_after": t.balance_after,
                "created_at": t.created_at.isoformat(),
            }
            for t in txns
        ],
    }