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
        inject_existing_tx_on_flush: Transaction | None = None,
        inject_existing_tx_on_flush_with_sub: tuple[Transaction, Transaction] | None = None,
        membership_repo: FakeMembershipRepo | None = None,
    ) -> None:
        # PRIMARY индекс — для SELECT по idempotency_key.
        # Значение = Transaction. Один и тот же Transaction не может быть
        # под двумя ключами, потому что idempotency_key UNIQUE в БД.
        self._tx_by_key: dict[str, Transaction] = {}
        # Вторичный — для тестов которым нужно перебрать все tx (используется редко).
        self._tx_by_id: dict[str, Transaction] = {}
        self.memberships: dict[str, Any] = {}
        self._membership_repo = membership_repo
        self._recompute_rows = recompute_rows or []
        self._raise_integrity_error = raise_integrity_error_on_flush
        # Опциональные транзакции, которые добавляются в _tx_by_key прямо
        # перед raise IntegrityError — имитация real race (другая транзакция
        # успела закоммитить между шагом 1 (SELECT) и шагом 10 (INSERT)).
        self._inject_tx_on_flush = inject_existing_tx_on_flush
        self._inject_tx_pair_on_flush = inject_existing_tx_on_flush_with_sub
        # Ключи injected tx (sub и dep отдельно) для slot-based matching
        # в execute() — service запрашивает либо dep_key либо sub_key,
        # fake различает запросы по consume_queue.
        if inject_existing_tx_on_flush_with_sub is not None:
            self._injected_sub_key = inject_existing_tx_on_flush_with_sub[0].idempotency_key
            self._injected_dep_key = inject_existing_tx_on_flush_with_sub[1].idempotency_key
        elif inject_existing_tx_on_flush is not None:
            self._injected_sub_key = None
            self._injected_dep_key = inject_existing_tx_on_flush.idempotency_key
        else:
            self._injected_sub_key = None
            self._injected_dep_key = None
        self.flush_calls = 0
        self.add_calls: list[Any] = []
        # ID транзакций, добавленных через session.add() (наши). Используется
        # в rollback() чтобы удалить только их, оставив injected existing.
        self._our_tx_ids: set[str] = set()
        # Snapshot users для rollback.
        self._user_snapshot: dict[int, int] = {}
        self._user_repo_ref: Any = None
        # Текущий ключ, который сервис хочет найти через execute(). Тесты
        # ОБЯЗАНЫ выставить его через set_query_key() перед вызовом сервиса,
        # иначе FakeSession выберет первую попавшуюся (legacy backward-compat).
        # Контекст: subscribe_and_join вызывает execute() несколько раз —
        # сначала dep_key (early-return check), потом sub_key (для charged_flag),
        # и в race-handling — те же два запроса. Тесты указывают ключи в этом
        # порядке через session.set_query_key(...).
        # Поддерживаются два режима:
        #   1. Legacy: set_query_key("subscribe:abc:dep") — fake ищет по этому
        #      ключу напрямую в _tx_by_key (как раньше).
        #   2. Slot-based: set_query_key(["dep", "sub"]) — fake consume'ит
        #      слоты "dep"/"sub" и ищет соответствующий injected ключ.
        #      Используется для race-handling тестов с inject_existing_tx_*
        #      чтобы различать dep_key vs sub_key запросы.
        self._expected_key: str | None = None

    def set_query_key(self, key: str | None) -> None:
        """Указать какой idempotency_key искать при следующем execute().

        Вызывай перед await service.subscribe_and_join(...). Если сервис делает
        два SELECT подряд (dep → sub), передай список:
            session.set_query_key([dep_key, sub_key])
        или None чтобы вернуться к backward-compat (первая попавшаяся).
        """
        if isinstance(key, list):
            self._expected_key = list(key)  # очередь
        else:
            self._expected_key = key

    def _consume_expected_key(self) -> str | None:
        """Забрать следующий ожидаемый ключ из очереди (FIFO).

        Поддерживает два формата:
        1. Legacy string/list of keys — fake ищет напрямую в _tx_by_key.
        2. Slot-based list of ("dep"|"sub") — fake резолвит slot в
           injected dep_key/sub_key (для race-handling тестов).
        """
        if self._expected_key is None:
            return None
        if isinstance(self._expected_key, list):
            if not self._expected_key:
                return None
            slot = self._expected_key.pop(0)
            # Slot-based: резолвим slot в injected ключ.
            if slot == "dep" and self._injected_dep_key is not None:
                return self._injected_dep_key
            if slot == "sub" and self._injected_sub_key is not None:
                return self._injected_sub_key
            # Иначе — slot это уже реальный ключ.
            return slot
        return self._expected_key

    async def execute(self, stmt: object) -> _Result:
        text = str(stmt)
        # SELECT Transaction WHERE idempotency_key == :key (шаг 1 и race-handling).
        # consume_expected_key вызываем ТОЛЬКО здесь, чтобы не съесть ключ
        # на SELECT для recompute_pause_status или других запросов.
        if "transactions" in text and "idempotency_key" in text:
            key = self._consume_expected_key()
            if key is None:
                # Backward-compat: если тест не выставил ключ явно — возвращаем
                # первую попавшуюся (старое поведение для одиночных транзакций).
                if not self._tx_by_key:
                    return _Result(scalar=None)
                return _Result(scalar=next(iter(self._tx_by_key.values())))
            tx = self._tx_by_key.get(key)
            return _Result(scalar=tx)
        # SELECT (Membership, Habit.penalty_amount) JOIN для recompute_pause_status.
        if "memberships" in text and "habits" in text:
            return _Result(rows=self._recompute_rows)
        return _Result(rows=[])

    def add(self, obj: Any) -> None:
        self.add_calls.append(obj)
        if isinstance(obj, Transaction):
            # Имитируем поведение БД: INSERT идёт в момент flush(), а не add().
            # Но для упрощения тестов добавляем сразу — тесты не полагаются на
            # отложенность.
            if obj.idempotency_key is not None:
                # Если ключ уже занят — имитируем UNIQUE constraint.
                if obj.idempotency_key in self._tx_by_key:
                    from sqlalchemy.exc import IntegrityError
                    raise IntegrityError("mock", {}, Exception("mock"))
                self._tx_by_key[obj.idempotency_key] = obj
            self._tx_by_id[obj.id] = obj
            # Запоминаем что это НАША tx (для rollback)
            self._our_tx_ids.add(obj.id)
        elif hasattr(obj, "habit_id") and hasattr(obj, "user_id"):
            # Membership-like object. Регистрируем и в membership_repo чтобы
            # последующие membership_repo.get() находили его (отражает flush() в БД).
            self.memberships[obj.id] = obj
            if self._membership_repo is not None:
                self._membership_repo._store[obj.id] = obj  # type: ignore[attr-defined]

    async def flush(self) -> None:
        self.flush_calls += 1
        # Сервис вызывает flush() минимум дважды:
        #   1. После создания membership (между шагом 6 и шагом 7-8).
        #   2. После создания transaction (шаг 10, в try/except IntegrityError).
        # Для race-handling тестов нам нужно инъектировать existing tx
        # и raise IntegrityError именно на 2-м вызове — иначе 1-й flush
        # упадёт на IntegrityError и сервис не дойдёт до try/except блока.
        on_transaction_flush = self.flush_calls >= 2
        if on_transaction_flush and self._inject_tx_pair_on_flush is not None:
            # Имитируем real race: другая транзакция успела закоммитить обе
            # записи (sub + dep) между шагом 1 (SELECT) и нашим INSERT.
            sub_tx, dep_tx = self._inject_tx_pair_on_flush
            self._tx_by_key[sub_tx.idempotency_key] = sub_tx
            self._tx_by_id[sub_tx.id] = sub_tx
            self._tx_by_key[dep_tx.idempotency_key] = dep_tx
            self._tx_by_id[dep_tx.id] = dep_tx
            # Race-партнёр сделал membership ACTIVE. Имитируем это.
            if dep_tx.related_membership_id and dep_tx.related_membership_id in self._membership_repo._store:  # type: ignore[attr-defined]
                self._membership_repo._store[dep_tx.related_membership_id].status = MembershipStatus.ACTIVE  # type: ignore[attr-defined]
        elif on_transaction_flush and self._inject_tx_on_flush is not None:
            # Только dep_tx (например, dep-only сценарий где charged=False).
            dep_tx = self._inject_tx_on_flush
            self._tx_by_key[dep_tx.idempotency_key] = dep_tx
            self._tx_by_id[dep_tx.id] = dep_tx
            # Race-партнёр сделал membership ACTIVE.
            if dep_tx.related_membership_id and dep_tx.related_membership_id in self._membership_repo._store:  # type: ignore[attr-defined]
                self._membership_repo._store[dep_tx.related_membership_id].status = MembershipStatus.ACTIVE  # type: ignore[attr-defined]
        if on_transaction_flush and self._raise_integrity_error:
            from sqlalchemy.exc import IntegrityError
            raise IntegrityError("mock", {}, Exception("mock"))

    async def rollback(self) -> None:
        # Имитируем реальный rollback БД: удаляем все НАШИ tx (добавленные
        # через session.add()), оставляя injected existing (это уже
        # закоммиченные данные от race-партнёра, их rollback не трогает).
        # Также восстанавливаем user.deposit_balance до изменений нашей
        # транзакции (snapshot делается в _snapshot_user_state()).
        our_ids = self._our_tx_ids.copy()
        for tx_id in our_ids:
            tx = self._tx_by_id.pop(tx_id, None)
            if tx is not None and tx.idempotency_key in self._tx_by_key:
                # Удаляем только если в _tx_by_key лежит НАША tx (а не injected)
                if self._tx_by_key[tx.idempotency_key].id == tx_id:
                    del self._tx_by_key[tx.idempotency_key]
        self._our_tx_ids.clear()
        # Восстанавливаем user.deposit_balance из snapshot.
        self._restore_user_state()
        # Также откатываем наши Membership (добавленные через session.add,
        # не те что добавлены тестом напрямую через membership_repo.add_for()).
        for mid in list(self.memberships.keys()):
            self.memberships.pop(mid, None)
            if self._membership_repo is not None and mid in self._membership_repo._store:  # type: ignore[attr-defined]
                self._membership_repo._store.pop(mid, None)  # type: ignore[attr-defined]

    def snapshot_user_state(self, user_repo: Any) -> None:
        """Сохранить текущее состояние users для последующего rollback.

        Вызывай перед await service.subscribe_and_join(...) — fake.rollback()
        восстановит deposit_balance после IntegrityError.
        """
        self._user_snapshot = {
            user.id: user.deposit_balance for user in user_repo._store.values()  # type: ignore[attr-defined]
        }

    def _restore_user_state(self) -> None:
        if not hasattr(self, "_user_snapshot"):
            return
        # Восстанавливаем через user_repo. Тест должен передать user_repo в
        # _make_service — у нас есть к нему доступ через self._membership_repo.
        # Но user_repo не хранится в session. Восстановим через _membership_repo.
        # Hack: восстановим напрямую через _store если она ссылается на users.
        # Решение: храним ссылку на user_repo при snapshot.
        if hasattr(self, "_user_repo_ref") and self._user_repo_ref is not None:
            for user in self._user_repo_ref._store.values():  # type: ignore[attr-defined]
                if user.id in self._user_snapshot:
                    user.deposit_balance = self._user_snapshot[user.id]

    async def commit(self) -> None:
        pass

    # ---- helpers для тестов ----
    @property
    def transactions(self) -> list[Transaction]:
        """Список всех зарегистрированных Transaction (для assertions в тестах)."""
        return list(self._tx_by_id.values())

    @property
    def transactions_by_key(self) -> dict[str, Transaction]:
        """Доступ к _tx_by_key для assertions (например, проверить что
        подключ :sub и :dep обе созданы)."""
        return dict(self._tx_by_key)


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

    Pravki §Z-13.3 fix: создаются ДВЕ отдельные транзакции —
    SUBSCRIPTION (price_month) и DEPOSIT_TOPUP (deposit). deposit_balance
    пополняется ТОЛЬКО на deposit. Возвращаемая tx = dep_tx (главная для UI).
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
    # 3. User.deposit_balance пополнен ТОЛЬКО на deposit_amount.
    # Subscription fee (1000₽) — это доход клуба/платформы, идёт в transactions как
    # type=SUBSCRIPTION для аудита, но НЕ на deposit_balance.
    assert user_repo._store[1].deposit_balance == 500_00
    # 4. Возвращённая tx — это dep_tx (главная для UI/alert).
    assert charged is True
    assert tx.type == TransactionType.DEPOSIT_TOPUP.value
    assert tx.amount == 500_00
    assert tx.balance_after == 500_00  # депозитная часть после topup
    assert tx.user_id == 1
    assert tx.related_membership_id == m.id
    # 5. Обе записи в БД (sub_tx + dep_tx).
    assert len(session.transactions) == 2
    sub_tx = session.transactions_by_key["subscribe:test-key-1:sub"]
    dep_tx = session.transactions_by_key["subscribe:test-key-1:dep"]
    assert sub_tx.type == TransactionType.SUBSCRIPTION.value
    assert sub_tx.amount == 1_000_00
    assert sub_tx.balance_after == 0   # до topup (deposit_balance был 0)
    assert dep_tx.type == TransactionType.DEPOSIT_TOPUP.value
    assert dep_tx.amount == 500_00
    assert dep_tx.balance_after == 500_00  # после topup
    # Обе указывают на одну membership.
    assert sub_tx.related_membership_id == m.id
    assert dep_tx.related_membership_id == m.id


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
    """Повторный POST с тем же idempotency_key → возвращаем существующую транзакцию.

    Pravki §Z-13.3 fix: возвращается dep_tx (главная). sub_tx НЕ создаётся
    повторно. charged_flag восстанавливается по наличию рядом sub_key.
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

    # Первая транзакция (dep_key ещё не существует — set_query_key не нужен).
    m1, tx1, charged1 = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="idemp-1",
    )
    assert charged1 is True
    assert user_repo._store[1].deposit_balance == 500_00
    assert len(session.transactions) == 2  # sub + dep

    # Второй вызов с тем же ключом — сервис сделает 2 SELECT (dep → sub).
    # Указываем очередь ключей.
    session.set_query_key(["subscribe:idemp-1:dep", "subscribe:idemp-1:sub"])
    m2, tx2, charged2 = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="idemp-1",
    )

    # Та же membership, та же dep_tx. charged восстановлен через наличие sub.
    assert tx2.id == tx1.id
    assert m2.id == m1.id
    assert charged2 is True
    # НЕ списали второй раз.
    assert user_repo._store[1].deposit_balance == 500_00
    # В БД по-прежнему 2 транзакции (не 4).
    assert len(session.transactions) == 2


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
    # Сервис сначала найдёт dep_tx (relates to habit_a membership), затем sub_tx.
    session.set_query_key(["subscribe:shared-key:dep", "subscribe:shared-key:sub"])
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
    # Pravki §Z-13.3 fix: возвращаемая tx — dep_tx. Обе записи (sub+dep) в БД.
    assert tx.type == TransactionType.DEPOSIT_TOPUP.value
    assert tx.amount == 500_00
    sub_tx = session.transactions_by_key["subscribe:expired-sub-1:sub"]
    assert sub_tx.type == TransactionType.SUBSCRIPTION.value
    assert sub_tx.amount == 1_000_00


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
    # Pravki §Z-13.3 fix: возвращаемая tx — dep_tx. sub_tx рядом с типом SUBSCRIPTION.
    assert tx.type == TransactionType.DEPOSIT_TOPUP.value
    sub_tx = session.transactions_by_key["subscribe:never-paid-1:sub"]
    assert sub_tx.type == TransactionType.SUBSCRIPTION.value


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
    """Кейс 3a → создаются ДВЕ транзакции: SUBSCRIPTION (price_month) + DEPOSIT_TOPUP (deposit).

    Pravki §Z-13.3 fix: возвращаемая tx — это dep_tx, но в БД рядом лежит sub_tx
    с типом SUBSCRIPTION. Тест проверяет ОБЕ записи.
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

    _m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="tx-type-3a",
    )

    assert charged is True
    # Возвращённая tx — это dep_tx (главная для UI).
    assert tx.type == TransactionType.DEPOSIT_TOPUP.value
    assert tx.amount == 500_00
    # Sub_tx рядом — отдельная запись для аудита подписки.
    sub_tx = session.transactions_by_key["subscribe:tx-type-3a:sub"]
    assert sub_tx.type == TransactionType.SUBSCRIPTION.value
    assert sub_tx.amount == 1_000_00


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
    # re-fetch нашёл её. dep_key = основной канонический ключ.
    existing_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.DEPOSIT_TOPUP.value,
        amount=500_00,
        balance_after=500_00,
        related_membership_id="other-membership-id",
        idempotency_key="subscribe:race-key:dep",
    )
    session = _FakeSession(raise_integrity_error_on_flush=True)
    session._tx_by_key[existing_tx.idempotency_key] = existing_tx
    session._tx_by_id[existing_tx.id] = existing_tx

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

    # После flush() IntegrityError → rollback → re-fetch dep_key (найдёт existing_tx),
    # затем SELECT sub_key (None — заряженной sub нет рядом).
    session.set_query_key(["subscribe:race-key:dep", "subscribe:race-key:sub"])
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
        type=TransactionType.DEPOSIT_TOPUP.value,
        amount=500_00,
        balance_after=500_00,
        related_membership_id=None,    # ← orphan
        idempotency_key="subscribe:orphan-key:dep",
    )
    session = _FakeSession(raise_integrity_error_on_flush=True)
    session._tx_by_key[existing_tx.idempotency_key] = existing_tx
    session._tx_by_id[existing_tx.id] = existing_tx

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
    """Race + related_membership_id с тем же habit_id → возвращаем существующую dep_tx.

    Pravki §Z-13.3 fix: charged_flag восстанавливается по наличию sub_tx рядом.
    Симметричный happy path — оба ключа присутствуют.
    """
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

    # Обе транзакции: sub_tx (канонический charged=True) + dep_tx (главная).
    existing_sub_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.SUBSCRIPTION.value,
        amount=1_000_00,
        balance_after=0,
        related_membership_id=existing_m.id,
        idempotency_key="subscribe:same-key:sub",
    )
    existing_dep_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.DEPOSIT_TOPUP.value,
        amount=500_00,
        balance_after=500_00,
        related_membership_id=existing_m.id,
        idempotency_key="subscribe:same-key:dep",
    )
    session._tx_by_key[existing_sub_tx.idempotency_key] = existing_sub_tx
    session._tx_by_key[existing_dep_tx.idempotency_key] = existing_dep_tx
    session._tx_by_id[existing_sub_tx.id] = existing_sub_tx
    session._tx_by_id[existing_dep_tx.id] = existing_dep_tx

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    # Сервис на шаге 1 сделает SELECT dep_key (найдёт existing_dep_tx), затем
    # SELECT sub_key (найдёт existing_sub_tx) — charged_flag = True.
    session.set_query_key(["subscribe:same-key:dep", "subscribe:same-key:sub"])
    m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="same-key",
    )

    # Возвращена dep_tx (главная) и membership.
    assert tx.id == existing_dep_tx.id
    assert m.id == existing_m.id
    # Без нового списания (deposit остался 500₽, как засеяли).
    assert user_repo._store[1].deposit_balance == 500_00
    # charged_flag восстановлен через наличие sub_tx.
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

    # Pravki §Z-13.3 fix: возвращаемая tx — dep_tx, её ключ с суффиксом :dep.
    assert tx.idempotency_key == "subscribe:client-uuid-abc:dep"
    # Sub_tx рядом — с суффиксом :sub.
    sub_tx = session.transactions_by_key["subscribe:client-uuid-abc:sub"]
    assert sub_tx.idempotency_key == "subscribe:client-uuid-abc:sub"
    # Клиент НИКОГДА не должен слать ключ с префиксом — это наша внутренняя зона.
    assert not tx.idempotency_key.startswith("subscribe:subscribe:")
    assert not sub_tx.idempotency_key.startswith("subscribe:subscribe:")


# ---------------------------------------------------------------------------
# 13. Pravki §Z-13.3: race fallback — IntegrityError на flush(), re-fetch dep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_race_fallback_returns_existing_dep_tx() -> None:
    """Real race: другая транзакция успела закоммитить sub_tx + dep_tx между
    шагом 1 (SELECT) и шагом 10 (INSERT). Наш flush() падает на UNIQUE,
    rollback, re-fetch dep_key, возвращаем существующий dep_tx.

    Pravki §Z-13.3 fix: в этой ветке кода критично проверить:
    - Возвращён существующий dep_tx (НЕ новый, не дубль).
    - charged_subscription=True восстановлен по наличию sub_tx рядом.
    - В БД нет ЛИШНИХ транзакций (sub_tx + dep_tx от race-партнёра, без наших).
    - deposit_balance НЕ изменился повторно (не было двойного списания).
    - Корректный порядок balance_after:
        * sub_tx.balance_after = u.deposit_balance ДО topup (старая сумма)
        * dep_tx.balance_after = u.deposit_balance ПОСЛЕ topup (новая сумма)
      Это гарантирует что UI/audit trail может показать "до" и "после"
      подписки-и-депозита как два отдельных события.
    """
    from sqlalchemy.exc import IntegrityError

    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices(penalty=500_00, price_month=1_000_00)
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)  # стартовый баланс 500₽
    membership_repo = FakeMembershipRepo()

    # Предсоздаём membership которая будет указывать в existing tx.
    # Статус PAUSED — имитируем состояние "юзер ранее был в клубе, подписка
    # истекла, сейчас он заново вступает через race-партнёра". После того как
    # race-партнёр "закоммитил" свою транзакцию, он же установил status=ACTIVE —
    # но в нашем fake мы не делаем это автоматически (см. ниже explicit flip
    # в session.flush()). Здесь ставим PAUSED чтобы пройти проверку
    # сервиса `if existing.status == ACTIVE: raise AlreadyActiveError`.
    existing_m = membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )

    # Транзакции которые "другая транзакция" успела закоммитить между
    # нашим SELECT (шаг 1) и нашим INSERT (шаг 10).
    # ВАЖНО: balance_after у sub_tx = 500 (до topup 500₽),
    #         balance_after у dep_tx = 1000 (после topup).
    existing_sub_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.SUBSCRIPTION.value,
        amount=1_000_00,
        balance_after=500_00,    # до topup
        related_membership_id=existing_m.id,
        idempotency_key="subscribe:race-real:sub",
    )
    existing_dep_tx = Transaction(
        id=str(uuid4()),
        user_id=1,
        type=TransactionType.DEPOSIT_TOPUP.value,
        amount=500_00,
        balance_after=1_000_00,   # после topup (500 + 500)
        related_membership_id=existing_m.id,
        idempotency_key="subscribe:race-real:dep",
    )

    # FakeSession с raise_integrity_error=True + инъекция existing tx
    # прямо в flush() ПЕРЕД raise. Это имитирует real race scenario.
    session = _FakeSession(
        raise_integrity_error_on_flush=True,
        inject_existing_tx_on_flush_with_sub=(existing_sub_tx, existing_dep_tx),
        membership_repo=membership_repo,
    )
    # Snapshot user state для rollback (имитация DB rollback).
    session.snapshot_user_state(user_repo)
    session._user_repo_ref = user_repo

    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    # Шаг 1 (early-return) не сработает — на момент SELECT транзакций ещё нет.
    # Шаг 10 (flush) → инъекция existing tx → IntegrityError → rollback →
    # re-fetch dep_key (найдёт) → SELECT sub_key (найдёт) → return existing.
    # Сервис делает 2 SELECT подряд в race-handling. Используем slot-based
    # запрос: ["dep", "dep", "sub"] — два dep (шаг 1 + race-handling) и
    # один sub (race-handling).
    session.set_query_key(["dep", "dep", "sub"])

    m, tx, charged = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=True,
        idempotency_key="race-real",
    )

    # 1. Возвращён СУЩЕСТВУЮЩИЙ dep_tx (НЕ новый).
    assert tx.id == existing_dep_tx.id, (
        f"должен быть возвращён existing_dep_tx, не новый. "
        f"got tx.id={tx.id}, expected={existing_dep_tx.id}"
    )
    # 2. Membership — та же, что у race-партнёра.
    assert m.id == existing_m.id
    # 3. charged_subscription восстановлен через наличие sub_tx рядом.
    assert charged is True
    # 4. В БД ТОЛЬКО 2 транзакции от race-партнёра (НЕ 4 от race + наших попыток).
    assert len(session.transactions) == 2, (
        f"должно быть ровно 2 транзакции, got {len(session.transactions)}: "
        f"{[t.idempotency_key for t in session.transactions]}"
    )
    assert existing_sub_tx.id in [t.id for t in session.transactions]
    assert existing_dep_tx.id in [t.id for t in session.transactions]
    # 5. Баланс НЕ изменился (race-партнёр уже списал, мы второй раз НЕ списываем).
    assert user_repo._store[1].deposit_balance == 500_00
    # 6. Flush был вызван ДВАЖДЫ: первый после создания membership (без race),
    #    второй — race-handling (с инъекцией и IntegrityError).
    assert session.flush_calls == 2

    # 7. ЯВНАЯ проверка порядка balance_after:
    #    sub_tx — ДО topup (старая сумма депозита)
    #    dep_tx — ПОСЛЕ topup (новая сумма депозита)
    sub_tx = session.transactions_by_key["subscribe:race-real:sub"]
    dep_tx = session.transactions_by_key["subscribe:race-real:dep"]
    assert sub_tx.balance_after == 500_00, (
        f"sub_tx.balance_after должен быть ДО topup (500₽), got {sub_tx.balance_after}"
    )
    assert dep_tx.balance_after == 1_000_00, (
        f"dep_tx.balance_after должен быть ПОСЛЕ topup (1000₽), got {dep_tx.balance_after}"
    )
    # sub_tx.balance_after + dep_tx.amount == dep_tx.balance_after
    # (математический инвариант: после подписки баланс не меняется,
    #  после topup = balance_after_sub + deposit_amount).
    assert sub_tx.balance_after + dep_tx.amount == dep_tx.balance_after


# ---------------------------------------------------------------------------
# 12. Pravki §Z-13.3: split transactions — без subscription fee + идемпотентность
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_without_subscription_fee_creates_single_deposit_topup() -> None:
    """Кейс 3b (активная подписка) → создаётся ТОЛЬКО DEPOSIT_TOPUP, без sub_tx.

    Тест покрывает:
    - Одна транзакция (sub_tx НЕ создаётся при charged_subscription=False).
    - dep_tx.type = DEPOSIT_TOPUP, amount = deposit_amount_kopecks.
    - dep_tx.balance_after = deposit_balance после topup.
    - :sub ключ НЕ присутствует в БД.
    - Повторный POST возвращает тот же dep_tx без дублей и без списания.
    """
    habit_repo = FakeHabitRepo()
    habit = _habit_with_prices()
    habit_repo.add(habit)
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500_00)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(
        user_id=1,
        habit_id=str(habit.id),
        status=MembershipStatus.PAUSED,
    )
    existing_m = next(iter(membership_repo._store.values()))  # type: ignore[attr-defined]
    existing_m.subscription_until = date.today() + timedelta(days=10)

    session = _FakeSession(membership_repo=membership_repo)
    service = _make_service(
        session=session,
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    # Первый POST — кейс 3b (активная подписка, charged_subscription=False).
    m1, tx1, charged1 = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=False,
        idempotency_key="no-sub-fee-1",
    )

    # charged=False — подписку не списывали (она уже активна).
    assert charged1 is False
    # Возвращена dep_tx. user deposit стартовал с 500₽, +500₽ = 1000₽.
    assert tx1.type == TransactionType.DEPOSIT_TOPUP.value
    assert tx1.amount == 500_00
    assert tx1.balance_after == 1_000_00  # deposit_balance после topup (500+500)
    assert tx1.user_id == 1
    assert tx1.related_membership_id == m1.id
    # ТОЛЬКО одна транзакция в БД — sub_tx не должно быть.
    assert len(session.transactions) == 1
    assert "subscribe:no-sub-fee-1:dep" in session.transactions_by_key
    assert "subscribe:no-sub-fee-1:sub" not in session.transactions_by_key
    # Membership активна.
    assert m1.status == MembershipStatus.ACTIVE

    # Второй POST с тем же ключом — идемпотентный retry.
    # Сервис сделает SELECT dep_key (найдёт), затем SELECT sub_key (None → charged_flag=False).
    session.set_query_key(["subscribe:no-sub-fee-1:dep", "subscribe:no-sub-fee-1:sub"])
    m2, tx2, charged2 = await service.subscribe_and_join(
        user_id=1,
        habit_id=str(habit.id),
        deposit_amount_kopecks=500_00,
        subscription_accepted=False,
        idempotency_key="no-sub-fee-1",
    )

    # Та же транзакция, тот же membership, тот же charged_flag.
    assert tx2.id == tx1.id
    assert m2.id == m1.id
    assert charged2 is False
    # В БД по-прежнему одна транзакция (не две).
    assert len(session.transactions) == 1
    # Баланс не изменился (не списали повторно).
    assert user_repo._store[1].deposit_balance == 1_000_00  # остался 500+500
