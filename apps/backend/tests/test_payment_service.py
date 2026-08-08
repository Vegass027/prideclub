from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import MembershipStatus
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
        self.users: dict[int, Any] = {}
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
        if "memberships" in text and "users.id" not in text:
            for m in self.memberships.values():
                return _Result(scalar=m)
            return _Result(scalar=None)
        if "users" in text and "deposit_balance" in text:
            for u in self.users.values():
                return _Result(scalar=u)
            return _Result(scalar=None)
        return _Result(rows=[])


def _seed_user_for_payment(user_repo: _FakeUserRepo, session: _FakeSession, user_id: int) -> Any:
    """Создаёт User в user_repo и session.users (для idempotency_/subscription тестов)."""
    from app.models.user import User

    u = User(id=user_id, first_name=f"u{user_id}", deposit_balance=0)
    user_repo._store[user_id] = u  # type: ignore[attr-defined]
    session.users[user_id] = u
    return u


@pytest.mark.asyncio
async def test_payment_idempotent() -> None:
    session = _FakeSession()
    user_repo = _FakeUserRepo()
    _seed_user_for_payment(user_repo, session, user_id=1)

    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]

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
    user_repo = _FakeUserRepo()
    _seed_user_for_payment(user_repo, session, user_id=42)

    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]

    initial_until = date(2026, 2, 1)
    m = Membership(
        id=str(uuid4()),
        user_id=42,
        habit_id="h1",
        status=MembershipStatus.ACTIVE,
        subscription_until=initial_until,
    )
    session.memberships[m.id] = m

    # хакнем execute — после первого lookup existing = None
    original_execute = session.execute

    async def execute_with_membership(stmt):
        text = str(stmt)
        if "transactions" in text and "idempotency_key" in text:
            return _Result(scalar=None)
        if "memberships" in text and "users.id" not in text:
            return _Result(scalar=m)
        if "users" in text and "deposit_balance" in text:
            return _Result(scalar=None)
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


# ---------------------------------------------------------------------------
# U4 (обновлён для PR #1): lock_for_update на USER при обработке платежей.
# Используем фейковый UserRepository, который фиксирует вызовы lock'а.
# ---------------------------------------------------------------------------


class _FakeUserRepo:
    """Зеркалит прод-API UserRepository, но без реального SELECT.

    Записывает последовательность вызовов lock'ов, чтобы тест мог проверить
    контракт блокировки. Хранит users в dict по user_id.
    """

    def __init__(self, store: dict[int, Any] | None = None) -> None:
        self._store: dict[int, Any] = store or {}
        self.lock_calls: list[int] = []

    async def lock_for_update(self, user_id: int) -> Any | None:
        self.lock_calls.append(user_id)
        return self._store.get(user_id)

    async def get(self, user_id: int) -> Any | None:
        return self._store.get(user_id)


@pytest.mark.asyncio
async def test_payment_acquires_lock_on_user() -> None:
    """PR #1: PaymentService лочит USER (а не membership) для deposit_topup.

    Защита от гонки webhook'ов одного юзера (subscription_renewal + topup).
    """
    from app.models.user import User

    u = User(id=1, first_name="u1", deposit_balance=0)
    user_repo = _FakeUserRepo({1: u})

    session = _FakeSession()
    session.users[1] = u

    # Создаём existing membership чтобы не упёрлись в "create new".
    m = Membership(
        id=str(uuid4()),
        user_id=1,
        habit_id="h1",
        status=MembershipStatus.ACTIVE,
    )
    session.memberships[m.id] = m

    # Хакнем execute чтобы get_for_user_in_habit нашёл membership.
    original_execute = session.execute

    async def execute_with_membership(stmt):
        text = str(stmt)
        if "memberships" in text and "users.id" not in text:
            return _Result(scalar=m)
        if "users" in text and "deposit_balance" in text:
            return _Result(scalar=u)
        if "transactions" in text and "idempotency_key" in text:
            return _Result(scalar=None)
        return await original_execute(stmt)

    session.execute = execute_with_membership  # type: ignore[assignment]

    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]

    await service.confirm_deposit_topup(
        charge_id="charge-lock-existing",
        user_id=1,
        habit_id="h1",
        amount_kopecks=10_000,
    )

    # Должен быть ровно один lock на user.
    assert user_repo.lock_calls == [1], (
        "должен быть ровно один lock_for_update на user перед += "
        "депозита — иначе гонка webhook'ов теряет деньги"
    )
    assert u.deposit_balance == 10_000


@pytest.mark.asyncio
async def test_payment_creates_membership_if_missing() -> None:
    """Если membership нет — _apply создаёт её и продолжает.

    После PR #1 user-lock первичен (user всегда есть). Membership может
    отсутствовать (legacy topup создаёт membership под капотом).
    """
    from app.models.user import User

    u = User(id=1, first_name="u1", deposit_balance=0)
    user_repo = _FakeUserRepo({1: u})

    session = _FakeSession()
    session.users[1] = u

    # Хакнем execute — get_for_user_in_habit возвращает None → PaymentService
    # должен создать membership, зафлашить, и продолжить.
    original_execute = session.execute

    async def execute_membership_none(stmt):
        text = str(stmt)
        if "memberships" in text and "users.id" not in text:
            return _Result(scalar=None)
        if "users" in text and "deposit_balance" in text:
            return _Result(scalar=u)
        if "transactions" in text and "idempotency_key" in text:
            return _Result(scalar=None)
        return await original_execute(stmt)

    session.execute = execute_membership_none  # type: ignore[assignment]

    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]

    tx = await service.confirm_deposit_topup(
        charge_id="charge-create-membership",
        user_id=1,
        habit_id="h1",
        amount_kopecks=50_000,
    )

    # Должна быть создана 1 membership и 1 транзакция.
    assert len(session.memberships) == 1
    assert len(session.transactions) == 1
    # Депозит юзера увеличен.
    assert u.deposit_balance == 50_000
    # transaction.balance_after = user.deposit_balance (а не membership).
    assert tx.balance_after == 50_000
