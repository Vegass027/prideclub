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
async def test_payment_skips_membership_creation_when_missing() -> None:
    """PR #2: депозит глобальный, membership-creation на topup УБРАНА.

    Pravki-deposit-sse.md §Z-2.5: после переноса депозита на users.deposit_balance
    membership больше не нужна для topup'а. Если юзер ещё не в клубе —
    транзакция записывается с `related_membership_id=None` (FK nullable).
    Membership создаётся явно через POST /habits/{id}/join.

    Старое поведение "создаём membership под капотом на topup" удалено
    полностью — это была legacy-логика эпохи deposit-на-membership.
    """
    from app.models.user import User

    u = User(id=1, first_name="u1", deposit_balance=0)
    user_repo = _FakeUserRepo({1: u})

    session = _FakeSession()
    session.users[1] = u

    # Хакнем execute — get_for_user_in_habit возвращает None (юзер не в клубе).
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
        charge_id="charge-no-membership",
        user_id=1,
        habit_id="h1",  # даже если habit_id указан, membership не создаётся
        amount_kopecks=50_000,
    )

    # НЕ создаётся membership — это было legacy.
    assert len(session.memberships) == 0, (
        "PaymentService НЕ должен создавать membership под капотом. "
        "Это устаревшая логика эпохи deposit-на-membership. См. §Z-2.5."
    )
    # Транзакция создаётся без related_membership_id.
    assert len(session.transactions) == 1
    assert session.transactions[tx.id].related_membership_id is None
    # Депозит юзера увеличен.
    assert u.deposit_balance == 50_000
    # transaction.balance_after = user.deposit_balance.
    assert tx.balance_after == 50_000


@pytest.mark.asyncio
async def test_payment_with_none_habit_id_skips_membership_lookup() -> None:
    """PR #2: habit_id=None (фронт не передаёт поле) → membership lookup skipped.

    Если у юзера уже есть membership для какого-то клуба — она не трогается.
    Если нет — никакая membership не создаётся. Депозит идёт глобально на user.
    """
    from app.models.user import User

    u = User(id=1, first_name="u1", deposit_balance=0)
    user_repo = _FakeUserRepo({1: u})

    session = _FakeSession()
    session.users[1] = u

    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]

    tx = await service.confirm_deposit_topup(
        charge_id="charge-no-habit",
        user_id=1,
        habit_id=None,  # фронт PR #2 шлёт undefined → Pydantic → None
        amount_kopecks=30_000,
    )

    assert len(session.memberships) == 0
    assert len(session.transactions) == 1
    assert tx.related_membership_id is None
    assert u.deposit_balance == 30_000


# ---------------------------------------------------------------------------
# Paused-member UX (feature/paused-member-ux): blocker test.
#
# Сценарий Sofia: deposit=0, membership в habit была paused
# (deposit < penalty). После topup recompute_pause_status должен
# выставить status=active (если новый deposit >= penalty).
#
# Если этот тест упадёт — кнопка "Пополнить" будет увеличивать
# баланс, но не возвращать юзера в активный статус. PR
# feature/paused-member-ux без этого фикса бесполезен.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topup_resumes_paused_membership() -> None:
    """Blocker test для feature/paused-member-ux.

    Воспроизводит сценарий Sofia из бага:
      - Юзер deposit=0, membership в habit paused (penalty 50₽).
      - Юзер жмёт «Пополнить» в Profile → POST /api/v1/payments/topup.
      - Ожидаем: deposit +=, membership.status = active.

    Логика в PaymentService._apply: после `u.deposit_balance += amount`
    вызывается `MembershipService.recompute_pause_status(user_id)`, который
    проходит по всем не-LEFT memberships и переставляет status на основе
    `deposit >= penalty_amount`. Этот тест ловит регрессию если кто-то
    удалит этот вызов (был уже случай в архиве — фиксируем контракт).
    """
    from app.models.user import User

    # 1. Юзер с пустым депозитом
    u = User(id=42, first_name="Sofia", deposit_balance=0)
    user_repo = _FakeUserRepo({42: u})

    # 2. Membership paused (deposit < penalty) — типичное состояние после
    #    apply_catch / topup до фикса UX
    m_paused = Membership(
        id=str(uuid4()),
        user_id=42,
        habit_id="habit-probegka",
        status=MembershipStatus.PAUSED,
    )

    session = _FakeSession()
    session.users[42] = u
    session.memberships[m_paused.id] = m_paused

    # 3. Хак session.execute:
    #    - transactions idempotency lookup → None (первый раз)
    #    - user lock → наш юзер
    #    - get_for_user_in_habit → m_paused (нужно для related_membership_id)
    #    - recompute_pause_status SELECT (Membership, penalty) → [(m_paused, 50_00)]
    original_execute = session.execute

    async def execute_paused_topup(stmt):
        text = str(stmt)
        if "transactions" in text and "idempotency_key" in text:
            return _Result(scalar=None)
        if "FROM memberships" in text and "habits" in text:
            # recompute_pause_status: SELECT m, h.penalty_amount FROM memberships JOIN habits
            return _Result(rows=[(m_paused, 50_00)])
        if "memberships" in text and "users.id" not in text:
            # get_for_user_in_habit — scalar lookup
            return _Result(scalar=m_paused)
        if "users" in text and "deposit_balance" in text:
            return _Result(scalar=u)
        return await original_execute(stmt)

    session.execute = execute_paused_topup  # type: ignore[assignment]

    # 4. Topup: +200₽ (deposit 0 → 200₽, penalty 50₽, должен стать active)
    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]
    await service.confirm_deposit_topup(
        charge_id="charge-sofia-resume",
        user_id=42,
        habit_id=None,  # глобальный topup из ProfilePage
        amount_kopecks=200_00,
    )

    # 5. Assertions
    assert u.deposit_balance == 200_00, (
        f"deposit should be 200₽ after topup, got {u.deposit_balance / 100:.2f}₽"
    )
    assert m_paused.status == MembershipStatus.ACTIVE, (
        "after topup, paused membership must be resumed to ACTIVE "
        "(deposit 200₽ >= penalty 50₽). Если тут PAUSED — "
        "recompute_pause_status не вызывается или неправильно пересчитывает. "
        "Без этого фикса кнопка 'Пополнить' в Profile не возвращает юзера в клуб."
    )


@pytest.mark.asyncio
async def test_topup_does_not_resume_when_still_below_penalty() -> None:
    """Topup меньше penalty → membership остаётся PAUSED.

    Защита от ложно-положительного test_topup_resumes_paused_membership.
    Если бы recompute_pause_status флипал PAUSED→ACTIVE безусловно —
    юзер с deposit=10₽ и penalty=50₽ стал бы ACTIVE и потерял бы штраф
    на первом же пропуске. Логика: status = ACTIVE ТОЛЬКО если
    deposit >= penalty_amount.
    """
    from app.models.user import User

    u = User(id=42, first_name="Sofia", deposit_balance=0)
    user_repo = _FakeUserRepo({42: u})
    m_paused = Membership(
        id=str(uuid4()),
        user_id=42,
        habit_id="habit-probegka",
        status=MembershipStatus.PAUSED,
    )
    session = _FakeSession()
    session.users[42] = u
    session.memberships[m_paused.id] = m_paused

    original_execute = session.execute

    async def execute_partial(stmt):
        text = str(stmt)
        if "transactions" in text and "idempotency_key" in text:
            return _Result(scalar=None)
        if "FROM memberships" in text and "habits" in text:
            return _Result(rows=[(m_paused, 50_00)])  # penalty 50₽
        if "memberships" in text and "users.id" not in text:
            return _Result(scalar=m_paused)
        if "users" in text and "deposit_balance" in text:
            return _Result(scalar=u)
        return await original_execute(stmt)

    session.execute = execute_partial  # type: ignore[assignment]

    service = PaymentService(session, user_repo=user_repo)  # type: ignore[arg-type]
    # Topup только 10₽ — меньше penalty 50₽ → membership должна остаться paused
    await service.confirm_deposit_topup(
        charge_id="charge-sofia-partial",
        user_id=42,
        habit_id=None,
        amount_kopecks=10_00,
    )

    assert u.deposit_balance == 10_00
    assert m_paused.status == MembershipStatus.PAUSED, (
        "topup меньше penalty не должен resume'ить membership "
        "(иначе юзер становится ACTIVE без coverage штрафа)"
    )
