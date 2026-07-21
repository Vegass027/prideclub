from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import MembershipStatus, TransactionType
from app.models.membership import Membership
from app.models.transaction import Transaction
from app.services.payment_service import PaymentService


class _Row:
    def __init__(self, scalar: Any = None):
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Result:
    def __init__(self, rows: list = None, scalar: Any = None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self) -> None:
        self.transactions: dict[str, Transaction] = {}
        self.memberships: dict[str, Membership] = {}
        self.committed = False
        self.rolled_back = False

    def add(self, obj) -> None:
        if isinstance(obj, Transaction):
            self.transactions[obj.id] = obj
        elif isinstance(obj, Membership):
            self.memberships[obj.id] = obj

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def execute(self, stmt) -> _Result:
        # Heuristic: ищем statements, которые ссылаются на Transaction/Membership.
        text = str(stmt)
        if "transactions" in text and "idempotency_key" in text:
            for tx in self.transactions.values():
                if tx.idempotency_key is not None:
                    return _Result(scalar=tx)
            return _Result(scalar=None)
        if "memberships" in text:
            for m in self.memberships.values():
                return _Result(scalar=m)
            return _Result(scalar=None)
        return _Result(rows=[])


@pytest.mark.asyncio
async def test_payment_idempotent() -> None:
    session = _FakeSession()
    service = PaymentService(session)

    tx1 = await service.confirm_deposit_topup(
        charge_id="charge-1",
        user_id=1,
        habit_id="h1",
        amount_kopecks=500_00,
    )
    assert tx1.amount == 500_00
    assert session.transactions[tx1.id].idempotency_key == "charge-1"

    # Повторный вызов с тем же charge_id → existing
    tx2 = await service.confirm_deposit_topup(
        charge_id="charge-1",
        user_id=1,
        habit_id="h1",
        amount_kopecks=500_00,
    )
    assert tx2.id == tx1.id
    # Транзакция осталась одна.
    assert len(session.transactions) == 1


@pytest.mark.asyncio
async def test_subscription_extends_until() -> None:
    session = _FakeSession()
    service = PaymentService(session)

    initial_until = date(2026, 2, 1)
    m = Membership(
        id=str(uuid4()),
        user_id=42,
        habit_id="h1",
        status=MembershipStatus.ACTIVE,
        subscription_until=initial_until,
        deposit_balance=0,
    )
    session.memberships[m.id] = m

    # хакнем execute — после первого lookup existing = None
    original_execute = session.execute

    async def execute_with_membership(stmt):
        text = str(stmt)
        if "transactions" in text and "idempotency_key" in text:
            return _Result(scalar=None)
        if "memberships" in text:
            return _Result(scalar=m)
        return await original_execute(stmt)

    session.execute = execute_with_membership  # type: ignore[assignment]

    tx = await service.confirm_subscription(
        charge_id="charge-2",
        user_id=42,
        habit_id="h1",
        amount_kopecks=1000_00,
        months=1,
    )
    assert tx.amount == 1000_00
    assert m.subscription_until == date(2026, 3, 3) or m.subscription_until > initial_until