from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.v1.users import TelegramUserDbDep
from app.core.deps import SessionDep
from app.models.transaction import Transaction
from app.models.user import User

router = APIRouter()


@router.get("/balance")
async def balance(
    user: TelegramUserDbDep,
    session: SessionDep,
) -> dict:
    # Pravki-deposit-sse.md §Z-2.1: депозит — на users.deposit_balance (один на юзера).
    # Один SELECT по users.id вместо SUM по всем memberships.
    deposit_balance = (await session.execute(
        select(User.deposit_balance).where(User.id == user.id)
    )).scalar_one_or_none() or 0

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
