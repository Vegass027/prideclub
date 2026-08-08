"""Тесты для MembershipService.subscribe_and_join (Pravki-subscribe-and-join.md §Z-18.1).

Покрытие по разделам плана:

1. Happy path (новый участник).
2. Idempotency (повторный POST с тем же ключом — return existing, без списания).
3. Server-side gate (матрица §Z-13.1: 4 ячейки subscription_active × subscription_accepted).
4. Validation (habit not found, archived, inactive, deposit < penalty, deposit = 0).
5. Already-active (membership.status == ACTIVE → 409).
6. Lock-ordering (проверка вызова lock_for_update на user).
7. Recompute pause status (PAUSED → ACTIVE если deposit хватает).
8. Transaction type semantics (§Z-13.3): кейс 3a → SUBSCRIPTION, кейс 3b → DEPOSIT_TOPUP.
9. subscription_until / joined_at semantics (§Z-13.2): joined_at не трогается при реактивации.

Использует FakeUserRepo + FakeHabitRepo + FakeMembershipRepo + кастомный _FakeSession
(как test_payment_service.py) — без зависимости от SQL. Это позволяет тестировать
SQL-операции (idempotency_key SELECT, INSERT membership/transaction, JOIN для recompute)
через heuristic string matching.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import MembershipStatus, TransactionType
from app.core.exceptions import (
    AlreadyActiveError,
    HabitInactiveError,
    HabitNotFoundError,
    IdempotencyConflictError,
    InsufficientDepositChoiceError,
    SubscriptionRequiredError,
)
from app.models.transaction import Transaction
from app.models.user import User
from app.services.membership_service import MembershipService
from tests.fakes import FakeHabitRepo, FakeMembershipRepo, FakeUserRepo, make_habit


# ---------------------------------------------------------------------------
# Fake session — поддерживает SELECT/INSERT для Transaction и Membership,
# плюс JOIN для recompute_pause_status через heuristic string matching.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, *, rows: list | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _FakeSession:
    """Fake session для subscribe_and_join.

    Поддерживает:
    - SELECT Transaction WHERE idempotency_key = :key
    - INSERT Membership / INSERT Transaction (через .add())
    - .flush() (no-op, но records что был вызван)
    - SELECT (Membership JOIN Habit) для recompute_pause_status
    - Опционально raise IntegrityError на flush для race-handling тестов.

    Membership, добавленные через session.add(), также регистрируются
    в переданном membership_repo — это отражает прод-поведение где
    flush() в БД делает membership видимой для последующих SELECT'ов
    через membership_repo.get().
    """

    def __init__(
        self,
        *,
        recompute_rows: list[tuple[Any, int]] | None = None,
        raise_integrity_error_on_flush: bool = False,
        membership_repo: FakeMembershipRepo | None = None,
    ) -> None:
        self.transactions: dict[str, Transaction] = {}
        self.memberships: dict[str, Any] = {}
        self._membership_repo = membership_repo
        self._recompute_rows = recompute_rows or []
        self._raise_integrity_error = raise_integrity_error_on_flush
        self.flush_calls = 0
        self.add_calls: list[Any] = []

    async def execute(self, stmt: object) -> _Result:
        text = str(stmt)
        # SELECT Transaction WHERE idempotency_key == :key (шаг 1 и race-handling).
        if "transactions" in text and "idempotency_key" in text:
            # Ищем первую транзакцию с совпадающим ключом.
            # В реальном SQLAlchemy фильтр по WHERE, но в фейке сравниваем напрямую
            # по тексту запроса через параметры — упрощаем через перебор.
            for tx in self.transactions.values():
                if tx.idempotency_key is not None:
                    return _Result(scalar=tx)
            return _Result(scalar=None)
        # SELECT (Membership, Habit.penalty_amount) JOIN для recompute_pause_status.
        if "memberships" in text and "habits" in text:
            return _Result(rows=self._recompute_rows)
        return _Result(rows=[])

    def add(self, obj: Any) -> None:
        self.add_calls.append(obj)
        if isinstance(obj, Transaction):
            self.transactions[obj.id] = obj
        elif hasattr(obj, "habit_id") and hasattr(obj, "user_id"):
            # Membership-like object. Регистрируем и в membership_repo чтобы
            # последующие membership_repo.get() находили его (отражает flush() в БД).
            self.memberships[obj.id] = obj
            if self._membership_repo is not None:
                self._membership_repo._store[obj.id] = obj  # type: ignore[attr-defined]

    async def flush(self) -> None:
        self.flush_calls += 1
        if self._raise_integrity_error:
            from sqlalchemy.exc import IntegrityError
            raise IntegrityError("mock", {}, Exception("mock"))

    async def rollback(self) -> None:
        pass

    async def commit(self) -> None:
        pass


def _seed_user(user_repo: FakeUserRepo, *, user_id: int, deposit: int) -> None:
    user_repo.add(User(id=user_id, first_name=f"u{user_id}", deposit_balance=deposit))


def _make_service(
    *,
    session: _FakeSession,
    user_repo: FakeUserRepo,
    habit_repo: FakeHabitRepo,
    membership_repo: FakeMembershipRepo,
) -> MembershipService:
    return MembershipService(
        session=session,  # type: ignore[arg-type]
        membership_repo=membership_repo,
        habit_repo=habit_repo,
        user_repo=user_repo,
    )


def _habit_with_prices(
    *, penalty: int = 500_00, price_month: int = 1_000_00
) -> Any:
    h = make_habit()
    h.penalty_amount = penalty
    h.price_month = price_month
    # make_habit() default is is_active=False (для list_active() фильтра);
    # в subscribe_and_join нужны активные клубы — явно включаем.
    h.is_active = True
    return h


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_creates_active_membership_and_charges_combined_payment() -> None:
    """Новый участник: списываем price_month + deposit, создаём ACTIVE membership.

    Verifies §Z-13.3: transaction type = SUBSCRIPTION (кейс 3a).
    """
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices(penalty=500_00, price_month=1_000_00)
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="test-key-1",
    )

    # 1. Membership создана с правильным статусом.
    assert m.status == MembershipStatus.ACTIVE
    assert m.user_id == 1
    assert m.habit_id == str(habit.id)
    # 2. subscription_until = today + 30 days.
    assert m.subscription_until == date.today() + timedelta(days=30)
    # 3. User.deposit_balance пополнен на price_month + deposit.
    assert user_repo._store[1].deposit_balance == 1_000_00 + 500_00
    # 4. Transaction создана с правильным типом и суммой.
    assert charged is True
    assert tx.type == TransactionType.SUBSCRIPTION.value
    assert tx.amount == 1_500_00
    assert tx.user_id == 1
    assert tx.related_membership_id == m.id
    assert tx.balance_after == 1_500_00
    # 5. Idempotency key имеет префикс "subscribe:".
    assert tx.idempotency_key == "subscribe:test-key-1"


@pytest.mark.asyncio
async def test_subscribe_creates_active_membership_for_brand_new_user() -> None:
    """existing is None → новая membership, subscription_until = today + 30d.

    joined_at проверяется в проде через server_default=func.now() — в фейке
    это поле не задаётся, поэтому assert'им только то, что задаёт сервис:
    status и subscription_until.
    """
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=42, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m, _tx, _charged = await service.subscribe_and_join(
        user_id=42,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="brand-new-1",
    )

    assert m.status == MembershipStatus.ACTIVE
    assert m.subscription_until == date.today() + timedelta(days=30)


# ---------------------------------------------------------------------------
# 2. Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_idempotent_with_same_key() -> None:
    """Повторный POST с тем же idempotency_key → возвращаем существующую транзакцию."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    # Первая транзакция.
    m1, tx1, charged1 = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="idemp-1",
    )
    assert charged1 is True
    assert user_repo._store[1].deposit_balance == 1_500_00

    # Второй вызов с тем же ключом — должен вернуть ту же транзакцию без нового списания.
    m2, tx2, charged2 = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="idemp-1",
    )

    # Та же транзакция и membership.
    assert tx2.id == tx1.id
    assert m2.id == m1.id
    # Но НЕ списали второй раз.
    assert user_repo._store[1].deposit_balance == 1_500_00


@pytest.mark.asyncio
async def test_subscribe_idempotency_conflict_with_different_habit() -> None:
    """Тот же idempotency_key но другой habit_id → 400 IdempotencyConflictError."""
    habit_repo = FakeHabitRepo()
    habit_a = _habit_with_prices(penalty=500_00)
    habit_a.id = "habit-a-id"
    habit_repo.add(habit_a)
    habit_b = _habit_with_prices(penalty=500_00)
    habit_b.id = "habit-b-id"
    habit_repo.add(habit_b)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    # Первый POST в habit_a.
    await service.subscribe_and_join(
        user_id=1,
        habit_id="habit-a-id",
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="shared-key",
    )

    # Второй POST с тем же ключом но в habit_b → conflict.
    with pytest.raises(IdempotencyConflictError):
        await service.subscribe_and_join(
            user_id=1,
            habit_id="habit-b-id",
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="shared-key",
        )


# ---------------------------------------------------------------------------
# 3. Server-side gate (матрица §Z-13.1: 4 ячейки)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_rejects_subscription_accepted_false_without_active_subscription() -> None:
    """existing is None (или нет активной подписки) + subscription_accepted=False → 422."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(SubscriptionRequiredError) as exc_info:
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),
            deposit_amount_kopecks=500_00,
            subscription_accepted=False,
            idempotency_key="no-sub-1",
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "subscription_required"


@pytest.mark.asyncio
async def test_subscribe_accepts_subscription_accepted_false_with_active_subscription() -> None:
    """Активная подписка (subscription_until >= today) + subscription_accepted=False → OK."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    # Существующая PAUSED membership с активной подпиской.
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    # Подменяем subscription_until напрямую.
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    existing_m.subscription_until = date.today() + timedelta(days=15)

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=False,    # ← False, но OK потому что подписка активна
        idempotency_key="active-sub-1",
    )

    # charged_subscription=False, списываем только deposit.
    assert charged is False
    assert tx.type == TransactionType.DEPOSIT_TOPUP.value
    assert tx.amount == 500_00
    # subscription_until НЕ изменился (кейс 3b, см. §Z-13.2).
    assert m.subscription_until == date.today() + timedelta(days=15)
    # Deposit пополнен только на deposit.
    assert user_repo._store[1].deposit_balance == 500_00 + 500_00
    # Membership реактивирована.
    assert m.status == MembershipStatus.ACTIVE


@pytest.mark.asyncio
async def test_subscribe_accepts_subscription_accepted_true_with_active_subscription() -> None:
    """Активная подписка + subscription_accepted=True → OK, charged=False (бэкенд решает)."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    existing_m.subscription_until = date.today() + timedelta(days=15)

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    _m, _tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,    # ← True, но бэкенд знает что подписка активна
        idempotency_key="active-sub-2",
    )

    # Заряд НЕ произошёл потому что подписка активна — бэкенд defensive-валидирует.
    assert charged is False


@pytest.mark.asyncio
async def test_subscribe_charges_full_when_subscription_expired() -> None:
    """Истёкшая подписка (subscription_until < today) → полная оплата."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    existing_m.subscription_until = date.today() - timedelta(days=1)  # вчера

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    _m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="expired-sub-1",
    )

    assert charged is True
    assert tx.type == TransactionType.SUBSCRIPTION.value
    assert tx.amount == 1_000_00 + 500_00


@pytest.mark.asyncio
async def test_subscribe_charges_full_when_subscription_was_never_paid() -> None:
    """LEFT без subscription_until → полная оплата."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.LEFT,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    existing_m.subscription_until = None  # никогда не платил

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    _m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="never-paid-1",
    )

    assert charged is True
    assert tx.type == TransactionType.SUBSCRIPTION.value


# ---------------------------------------------------------------------------
# 4. Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_rejects_deposit_below_penalty() -> None:
    """deposit < penalty → 422 insufficient_deposit_choice."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices(penalty=500_00)
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(InsufficientDepositChoiceError) as exc_info:
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),
            deposit_amount_kopecks=100_00,    # < penalty 500_00
            subscription_accepted=True,
            idempotency_key="low-deposit-1",
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "insufficient_deposit_choice"
    assert exc_info.value.extras == {
        "required_kopecks": 500_00,
        "chosen_kopecks": 100_00,
    }


@pytest.mark.asyncio
async def test_subscribe_rejects_unknown_habit() -> None:
    """habit_id не существует → 404 HabitNotFoundError."""
    habit_repo = FakeHabitRepo()
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(HabitNotFoundError):
        await service.subscribe_and_join(
            user_id=1,
            habit_id="does-not-exist",
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="unknown-habit-1",
        )


@pytest.mark.asyncio
async def test_subscribe_rejects_archived_habit() -> None:
    """habit.archived_at != None → 404 HabitNotFoundError (как в несуществующий)."""
    from datetime import datetime, timezone

    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit.archived_at = datetime.now(tz=timezone.utc)
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(HabitNotFoundError):
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="archived-1",
        )


@pytest.mark.asyncio
async def test_subscribe_rejects_inactive_habit() -> None:
    """habit.is_active=False → 409 HabitInactiveError."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit.is_active = False
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(HabitInactiveError) as exc_info:
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="inactive-1",
        )
    assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# 5. Already-active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_rejects_already_active_membership() -> None:
    """existing.status == ACTIVE → 409 AlreadyActiveError."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.ACTIVE,
    )

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(AlreadyActiveError) as exc_info:
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="already-active-1",
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "already_active"


# ---------------------------------------------------------------------------
# 6. Lock-ordering — бэкенд должен вызвать lock_for_update на user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_calls_lock_for_update_on_user() -> None:
    """После проверки idempotency и 3a/3b/3c — бэкенд вызывает user_repo.lock_for_update.

    FakeUserRepo._lock_calls — список user_id для каждого вызова lock_for_update.
    """
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="lock-test-1",
    )

    # User lock был взят ровно один раз.
    assert user_repo._lock_calls == [1]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 7. Recompute pause status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_triggers_recompute_pause_status() -> None:
    """subscribe_and_join вызывает recompute_pause_status(user_id) после мутации deposit.

    На практике recompute_pause_status делает SELECT (Membership JOIN Habit).
    Проверяем что session.execute был вызван с JOIN-запросом.
    """
    from app.models.membership import Membership as MembershipModel

    # Подготовим существующую PAUSED membership с penalty меньше текущего deposit.
    existing_m = MembershipModel(
        id=str(uuid4()),
        user_id=1,
        habit_id="habit-x",
        status=MembershipStatus.PAUSED,
        joined_at=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
    )
    # session.execute возвращает [(existing_m, 200_00)] — recompute видит эту membership.
    session = _FakeSession(recompute_rows=[(existing_m, 200_00)])

    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices(penalty=500_00, price_month=1_000_00)
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)  # 500₽ хватает на penalty 200₽ клуба X
    membership_repo = FakeMembershipRepo()
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="recompute-1",
    )

    # recompute должен был переключить PAUSED → ACTIVE (deposit 500₽ >= penalty 200₽).
    assert existing_m.status == MembershipStatus.ACTIVE


# ---------------------------------------------------------------------------
# 8. Transaction type semantics (§Z-13.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_active_subscription_creates_deposit_topup_transaction() -> None:
    """Регрессионный тест: кейс 3b → TransactionType.DEPOSIT_TOPUP, а НЕ SUBSCRIPTION.

    Защита от регрессии "вернули обратно type=SUBSCRIPTION для всех случаев".
    """
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    existing_m.subscription_until = date.today() + timedelta(days=10)

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    _m, tx, _charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=False,
        idempotency_key="tx-type-3b",
    )

    # КЛЮЧЕВАЯ инвариантность: type=DEPOSIT_TOPUP, а не SUBSCRIPTION.
    assert tx.type == TransactionType.DEPOSIT_TOPUP.value
    assert tx.type != TransactionType.SUBSCRIPTION.value
    assert tx.amount == 500_00


@pytest.mark.asyncio
async def test_subscribe_full_payment_creates_subscription_transaction() -> None:
    """Кейс 3a → TransactionType.SUBSCRIPTION (как исторически)."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices(penalty=500_00, price_month=1_000_00)
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    _m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="tx-type-3a",
    )

    assert charged is True
    assert tx.type == TransactionType.SUBSCRIPTION.value
    assert tx.amount == 1_500_00


# ---------------------------------------------------------------------------
# 9. subscription_until / joined_at semantics (§Z-13.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_reactivate_with_active_sub_does_not_change_subscription_until() -> None:
    """Кейс 3b: subscription_until остаётся прежним (не продлевается)."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    original_sub_until = date.today() + timedelta(days=20)
    original_joined_at = existing_m.joined_at
    existing_m.subscription_until = original_sub_until

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m, _tx, _charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=False,
        idempotency_key="reactivate-1",
    )

    # subscription_until НЕ изменился (§Z-13.2: остаётся как был).
    assert m.subscription_until == original_sub_until
    # joined_at тоже НЕ изменился.
    assert m.joined_at == original_joined_at


@pytest.mark.asyncio
async def test_subscribe_reactivate_with_expired_sub_extends_subscription_until() -> None:
    """Кейс 3a с истёкшей подпиской: subscription_until продлевается на 30 дней."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    original_joined_at = existing_m.joined_at
    existing_m.subscription_until = date.today() - timedelta(days=5)  # истекла

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m, _tx, _charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="extend-sub-1",
    )

    # subscription_until продлён на 30 дней от сегодня.
    assert m.subscription_until == date.today() + timedelta(days=30)
    # joined_at НЕ изменился (это дата первого вступления).
    assert m.joined_at == original_joined_at


# ---------------------------------------------------------------------------
# 10. Race-handling с habit_id-guard (из чекпоинта Z-13)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_race_handling_habit_id_mismatch_returns_idempotency_conflict() -> None:
    """Симуляция race: IntegrityError на flush, после rollback находим чужую транзакцию
    с тем же idempotency_key но другим habit_id → IdempotencyConflictError.

    Проверяет трёхуровневый guard из Z-13 фикса: scalar_one_or_none → orphan → habit_id check.
    """
    # Предзаполним session транзакцией с чужим habit_id, чтобы race-handling
    # re-fetch нашёл её.
    existing_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.SUBSCRIPTION.value,
        amount=1_500_00,
        balance_after=1_500_00,
        related_membership_id="other-membership-id",
        idempotency_key="subscribe:race-key",
    )
    session = _FakeSession(raise_integrity_error_on_flush=True)
    session.transactions[existing_tx.id] = existing_tx

    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    # Подсунем related_membership → habit_id чужой (другой клуб).
    other_membership = membership_repo.add_for(
        user_id=1,
        habit_id="different-habit-id",
        status=MembershipStatus.ACTIVE,
    )
    existing_tx.related_membership_id = other_membership.id

    with pytest.raises(IdempotencyConflictError):
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),  # ← пытаемся вступить в habit
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="race-key",
        )


@pytest.mark.asyncio
async def test_subscribe_race_handling_orphan_tx_returns_idempotency_conflict() -> None:
    """Race + related_membership_id=None → orphan guard срабатывает → IdempotencyConflictError."""
    existing_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.SUBSCRIPTION.value,
        amount=1_500_00,
        balance_after=1_500_00,
        related_membership_id=None,    # ← orphan
        idempotency_key="subscribe:orphan-key",
    )
    session = _FakeSession(raise_integrity_error_on_flush=True)
    session.transactions[existing_tx.id] = existing_tx

    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(IdempotencyConflictError):
        await service.subscribe_and_join(
            user_id=1,
            habit_id=str(habit.id),
            deposit_amount_kopecks=500_00,
            subscription_accepted=True,
            idempotency_key="orphan-key",
        )


@pytest.mark.asyncio
async def test_subscribe_race_handling_same_habit_returns_existing() -> None:
    """Race + related_membership_id с тем же habit_id → возвращаем существующую транзакцию."""
    session = _FakeSession(raise_integrity_error_on_flush=True)

    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()

    # Предсоздаём membership с правильным habit_id.
    existing_m = membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.ACTIVE,
    )

    existing_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.SUBSCRIPTION.value,
        amount=1_500_00,
        balance_after=1_500_00,
        related_membership_id=existing_m.id,
        idempotency_key="subscribe:same-key",
    )
    session.transactions[existing_tx.id] = existing_tx

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="same-key",
    )

    # Возвращена та же транзакция и membership.
    assert tx.id == existing_tx.id
    assert m.id == existing_m.id
    # Без нового списания (deposit остался 500₽, как засеяли).
    assert user_repo._store[1].deposit_balance == 500_00
    assert charged is True


# ---------------------------------------------------------------------------
# 11. Idempotency prefix (Pravki-subscribe-and-join.md §Z-14: префикс "subscribe:")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_idempotency_key_has_subscribe_prefix() -> None:
    """Ключ сохраняется в БД с префиксом "subscribe:" для изоляции от topup'ов."""
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)
    membership_repo = FakeMembershipRepo()
    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    _m, tx, _charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="client-uuid-abc",
    )

    # Префикс добавляется в сервисе (см. §Z-13 шаг 1).
    assert tx.idempotency_key == "subscribe:client-uuid-abc"
    # Клиент НИКОГДА не должен слать ключ с префиксом — это наша внутренняя зона.
    assert not tx.idempotency_key.startswith("subscribe:subscribe:")
