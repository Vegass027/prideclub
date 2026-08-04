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


# ---------------------------------------------------------------------------
# U4: FOR UPDATE на membership при обработке платежей.
# Используем фейковый MembershipRepository, который фиксирует вызовы lock'а.
# ---------------------------------------------------------------------------


class _FakeMembershipRepo:
    """Зеркалит прод-API MembershipRepository, но без реального SELECT.

    Записывает последовательность вызовов lock'ов, чтобы тест мог проверить
    контракт блокировки. Хранит memberships в dict по (user_id, habit_id).
    """

    def __init__(self, store: dict[tuple[int, str], Membership] | None = None) -> None:
        self._store: dict[tuple[int, str], Membership] = store or {}
        self.lock_calls: list[tuple[int, str]] = []

    async def lock_for_update_by_user_habit(
        self, user_id: int, habit_id: str
    ) -> Membership | None:
        self.lock_calls.append((user_id, habit_id))
        return self._store.get((user_id, habit_id))

    def add_membership(self, m: Membership) -> None:
        self._store[(m.user_id, str(m.habit_id))] = m


@pytest.mark.asyncio
async def test_payment_acquires_lock_on_existing_membership() -> None:
    """_apply должен идти через lock_for_update_by_user_habit (а не SELECT без lock)."""
    m = Membership(
        id=str(uuid4()),
        user_id=1,
        habit_id="h1",
        status=MembershipStatus.ACTIVE,
        deposit_balance=0,
    )
    repo = _FakeMembershipRepo({(1, "h1"): m})

    # Передаём явный фейковый session, чтобы PaymentService не упал
    # на неподдерживаемом execute() у _FakeSession (lock уже сделает repo).
    session = _FakeSession()
    service = PaymentService(session, membership_repo=repo)  # type: ignore[arg-type]

    await service.confirm_deposit_topup(
        charge_id="charge-lock-existing",
        user_id=1,
        habit_id="h1",
        amount_kopecks=10_000,
    )

    assert repo.lock_calls == [(1, "h1")], (
        "должен быть ровно один lock_for_update_by_user_habit перед += "
        "депозита — иначе гонка webhook'ов теряет деньги"
    )
    assert m.deposit_balance == 10_000


@pytest.mark.asyncio
async def test_payment_creates_and_re_locks_missing_membership() -> None:
    """Если membership нет — _apply создаёт, flush'ит, затем lock'ит повторно.

    Контракт: create-then-lock гарантирует атомарность относительно
    параллельного writer'а, который тоже пытается создать ту же membership.
    """
    # Сценарий: первый вызов lock'а возвращает None (membership ещё не создана),
    # затем в session.add() появляется новая Membership; второй вызов lock'а
    # её уже видит.
    class _SideEffectRepo(_FakeMembershipRepo):
        def __init__(self) -> None:
            super().__init__()
            self._created: list[Membership] = []

        async def lock_for_update_by_user_habit(
            self, user_id: int, habit_id: str
        ) -> Membership | None:
            self.lock_calls.append((user_id, habit_id))
            existing = self._store.get((user_id, habit_id))
            if existing is not None:
                return existing
            # Имитируем flush из payment_service — после первого None
            # мы «внезапно видим» только что созданную Membership.
            if self._created:
                return self._created[0]
            return None

        def register_created(self, m: Membership) -> None:
            """PaymentService вызовет session.add() → дёрнем этот хук."""
            self._created.append(m)
            self._store[(m.user_id, str(m.habit_id))] = m

    repo = _SideEffectRepo()
    session = _FakeSession()

    # Перехватываем session.add: когда PaymentService добавляет новую
    # Membership — синхронизируем с фейковым репо.
    orig_add = session.add

    def intercept_add(obj: Any) -> None:
        orig_add(obj)
        if isinstance(obj, Membership):
            repo.register_created(obj)

    session.add = intercept_add  # type: ignore[assignment]

    service = PaymentService(session, membership_repo=repo)  # type: ignore[arg-type]

    await service.confirm_deposit_topup(
        charge_id="charge-create-then-lock",
        user_id=1,
        habit_id="h1",
        amount_kopecks=50_000,
    )

    # Должно быть 2 вызова lock: первый (None), второй (после flush).
    assert len(repo.lock_calls) == 2, (
        f"при отсутствии membership должно быть 2 lock'а (1-None, 2-after-flush); "
        f"получили {len(repo.lock_calls)}. Один = регресс к гонке webhook'ов"
    )
    assert repo.lock_calls[0] == (1, "h1")
    assert repo.lock_calls[1] == (1, "h1")