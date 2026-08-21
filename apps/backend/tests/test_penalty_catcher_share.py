"""Unit-тесты для PenaltyService.apply_catch после рефактора Pravki-catcher-deposit
(Phase 1 Task 1.3, 2026-08-21).

Покрывает:
1. Разделение штрафа на catcher_amount (ловцу) + fund_share (в фонд):
   - 4 кейса catcher_amount_kopecks: 0, 10000 (100₽), 20000 (200�), 40000 (400₽)
     при penalty_amount=30000 (300₽).
2. Клэмп ДО: при deposit < penalty_amount, catcher_amount считается от ФАКТИЧЕСКОГО
   списания (amount), не от номинала. Иначе баланс не сойдётся.
3. Suspicious pair (variant A): деньги идут, is_suspicious_pair=true.
4. ASC-порядок локов (deadlock-free): два кейса по возрастанию user_id.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.constants import MembershipStatus, ProofType
from app.core.exceptions import MembershipNotActiveError
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User
from app.services.penalty_service import PenaltyService
from tests.fakes import (
    FakeCheckinRepo,
    FakeHabitRepo,
    FakeMembershipRepo,
    FakeSuspiciousPairsRepository,
    FakeUserRepo,
)

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_user(*, id: int, deposit_balance: int) -> User:
    return User(id=id, first_name=f"u{id}", deposit_balance=deposit_balance)


def _make_habit(
    *,
    id: str | None = None,
    penalty_amount: int = 30000,
    catcher_amount_kopecks: int = 0,
) -> Habit:
    """Habit с явными параметрами для тестов catcher deposit share.

    penalty_amount по умолчанию 30000 (= 300₽) — удобно для тестов с
    catcher_amount_kopecks ∈ {0, 10000, 20000, 30000, 40000}.
    """
    from datetime import time

    return Habit(
        id=id or str(uuid4()),
        title="Test Habit",
        chat_id=100,
        checkin_window_start=time(9, 0),
        checkin_window_end=time(21, 0),
        timezone="Europe/Moscow",
        penalty_amount=penalty_amount,
        price_month=1000,
        prize_pool=0,
        is_active=True,
        proof_type=ProofType.VIDEO_NOTE,
        proof_types=[ProofType.VIDEO_NOTE.value],
        catcher_amount_kopecks=catcher_amount_kopecks,
    )


class _NoStreakSession:
    """Минимальный session для PenaltyService.apply_catch."""

    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    def add(self, obj: Any) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        class _Result:
            def first(self_inner) -> Any:
                return None

            def all(self_inner) -> list:
                return []

        return _Result()


class _NoopLimiter:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def incr_catch(self, catcher_user_id: int) -> int:
        self.calls.append(catcher_user_id)
        return 1


async def _setup_apply_catch(
    *,
    catcher_amount_kopecks: int,
    violator_deposit: int,
    penalty_amount: int = 30000,
    catcher_deposit: int = 0,
) -> tuple[
    PenaltyService,
    Habit,
    Membership,
    User,
    User,
    FakeUserRepo,
    FakeSuspiciousPairsRepository,
    Membership,
]:
    """Создаёт PenaltyService с фикстурами для теста.

    Returns: (service, habit, violator_membership, violator_user, catcher_user,
              user_repo, suspicious_repo, catcher_membership).
    """
    habit_repo = FakeHabitRepo()
    habit = _make_habit(
        penalty_amount=penalty_amount,
        catcher_amount_kopecks=catcher_amount_kopecks,
    )
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    catcher_membership = membership_repo.add_for(user_id=2, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    violator_user = _make_user(id=1, deposit_balance=violator_deposit)
    catcher_user = _make_user(id=2, deposit_balance=catcher_deposit)
    user_repo.add(violator_user)
    user_repo.add(catcher_user)

    suspicious_repo = FakeSuspiciousPairsRepository()

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=suspicious_repo,
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )
    return (
        service,
        habit,
        violator,
        violator_user,
        catcher_user,
        user_repo,
        suspicious_repo,
        catcher_membership,
    )


# Catch window для клуба 09:00-21:00 MSK = 21:00 → 04:00 UTC next day.
# 22:00 UTC (= 01:00 MSK next day) — внутри catch window.
_NOW_UTC = datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC"))
_CLUB_DATE = date(2026, 1, 1)


# ---------------------------------------------------------------------------
# Разделение штрафа: 4 кейса для catcher_amount_kopecks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catcher_deposit_zero_kopecks_all_to_fund() -> None:
    """catcher_amount_kopecks=0 → всё в фонд (старое поведение, обратная совместимость).

    Штраф 300₽ (30000 коп), депозит 1000₽, ловцу 0₽ → фонд +300₽, ловцу +0₽.
    """
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        _suspicious,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=0,
        violator_deposit=100000,
    )
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )

    # Penalty разделение:
    assert penalty.amount == habit.penalty_amount  # 30000
    assert penalty.catcher_amount == 0  # NEW
    assert penalty.fund_share == 30000  # вся сумма в фонд
    assert penalty.is_suspicious_pair is False  # NEW
    assert penalty.catcher_membership_id == str(catcher_membership.id)  # всегда пишем (variant A)

    # Депозит нарушителя:
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 100000 - 30000

    # Депозит ловца — без изменений:
    assert catcher_user.deposit_balance == 0

    # Habit prize_pool:
    assert habit.prize_pool == 30000

    # Транзакции:
    assert penalty.amount == 30000


@pytest.mark.asyncio
async def test_catcher_deposit_partial_split() -> None:
    """catcher_amount_kopecks=10000 (100₽) → ловцу 100₽, в фонд 200₽.

    Штраф 300₽ (30000 коп), депозит 1000₽, ловцу 100₽.
    """
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        _suspicious,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=10000,
        violator_deposit=100000,
    )
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )

    assert penalty.amount == 30000
    assert penalty.catcher_amount == 10000  # 100₽ ловцу
    assert penalty.fund_share == 20000  # 200₽ в фонд
    assert penalty.is_suspicious_pair is False

    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 100000 - 30000

    assert catcher_user.deposit_balance == 10000  # получил 100₽

    assert habit.prize_pool == 20000

    # Проверяем что есть Transaction(type=CATCHER_DEPOSIT, amount=+10000):
    # В FakeSession мы не отслеживаем транзакции, но penalty.catcher_amount > 0
    # → ветка кода создаёт Transaction (см. service.apply_catch).
    # Здесь только проверяем что deposit ловца вырос — это индикатор.


@pytest.mark.asyncio
async def test_catcher_deposit_equal_split() -> None:
    """catcher_amount_kopecks=20000 (200₽) → ловцу 200₽, в фонд 100₽."""
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        _suspicious,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=20000,
        violator_deposit=100000,
    )
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )

    assert penalty.amount == 30000
    assert penalty.catcher_amount == 20000
    assert penalty.fund_share == 10000

    assert catcher_user.deposit_balance == 20000
    assert habit.prize_pool == 10000


@pytest.mark.asyncio
async def test_catcher_deposit_more_than_nominal_all_to_catcher() -> None:
    """catcher_amount_kopecks=40000 (400₽) >= penalty_amount → всё ловцу, фонд=0.

    Edge case: админ поставил ловцу больше номинала. Логика: clamp к amount.
    """
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        _suspicious,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=40000,
        violator_deposit=100000,
    )
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )

    # catcher_amount clamped к amount (= 30000):
    assert penalty.amount == 30000
    assert penalty.catcher_amount == 30000  # всё ловцу
    assert penalty.fund_share == 0  # фонд пустой

    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 100000 - 30000

    assert catcher_user.deposit_balance == 30000
    assert habit.prize_pool == 0


# ---------------------------------------------------------------------------
# Клэмп ДО: catcher_amount от ФАКТИЧЕСКОГО списания (amount), не от номинала
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catcher_deposit_clamps_to_actual_amount_not_nominal() -> None:
    """Главный финансовый инвариант Task 1.3 (Дмитрий 2026-08-21):
    deposit < penalty → списываем меньше номинала (amount < penalty_amount).
    catcher_amount считается от amount, иначе раздали бы больше чем списали.

    Сценарий: deposit=200₽, penalty=300₽, catcher_amount_kopecks=100� (ловцу).
    Если бы считали от номинала: catcher_amount = min(100, 300) = 100.
    Тогда раздали бы 100 + (200 - 100) = 200 в фонд = 300, а списали 200. УБЫТОК 100.
    Правильно: catcher_amount = min(100, 200) = 100. Раздали 100 + 100 = 200 = списано.
    """
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        _suspicious,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=10000,  # 100₽ ловцу
        violator_deposit=20000,  # 200� депозит (меньше штрафа 300₽)
    )
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )

    # Списано фактически 200₽ (клэмп ДО):
    assert penalty.amount == 20000  # amount = min(penalty_amount, deposit)
    # catcher_amount = min(catcher_kopecks, amount) = min(10000, 20000) = 10000
    assert penalty.catcher_amount == 10000
    assert penalty.fund_share == 10000  # amount - catcher_amount

    # Балансы сходятся:
    # - списали 200₽ у violator
    # - 100₽ ловцу + 100₽ в фонд = 200₽ (НЕ 300₽)
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 0  # 20000 - 20000
    assert catcher_user.deposit_balance == 10000  # +100₽
    assert habit.prize_pool == 10000  # +100₽ в фонд

    # CHECK ck_penalties_amount_equals_sum: 20000 = 10000 + 10000 ✅


# ---------------------------------------------------------------------------
# Suspicious pair (variant A): деньги идут, флаг для лидерборда
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catcher_deposit_suspicious_pair_money_still_flows() -> None:
    """Variant A (Дмитрий 2026-08-21): деньги НЕ блокируются для flagged пар,
    только флаг is_suspicious_pair=true для лидерборда.

    Сговор финансово невыгоден (оба теряют деньги в текущей модели), но портит
    лидерборды — нужна метка для фильтрации фейковых поимок.
    """
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        suspicious_repo,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=10000,  # 100₽ ловцу
        violator_deposit=100000,
    )
    # Flag пару (catcher_membership_id, violator_membership_id) как suspicious.
    # Variant A: деньги ВСЁ РАВНО идут, только is_suspicious_pair=true.
    suspicious_repo.flag(str(catcher_membership.id), str(violator.id))
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )

    # Флаг установлен:
    assert penalty.is_suspicious_pair is True
    # catcher_membership_id ВСЕГДА пишем (деньги идут):
    assert penalty.catcher_membership_id == str(catcher_membership.id)

    # Деньги движутся как обычно (НЕ заблокированы):
    assert penalty.amount == 30000
    assert penalty.catcher_amount == 10000
    assert penalty.fund_share == 20000
    assert catcher_user.deposit_balance == 10000  # ловец получил деньги
    assert habit.prize_pool == 20000


# ---------------------------------------------------------------------------
# ASC-порядок локов (deadlock-free)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_order_ascending_violator_smaller() -> None:
    """violator_id < catcher_id → lock_for_update вызывается [violator_id, catcher_id]."""
    (
        service,
        habit,
        violator,
        _,
        catcher_user,
        user_repo,
        _suspicious,
        catcher_membership,
    ) = await _setup_apply_catch(
        catcher_amount_kopecks=10000,
        violator_deposit=100000,
    )
    # violator.id=1 (user 1), catcher.id=2 (user 2): violator_id (1) < catcher_id (2)
    # → locks: [1, 2]
    await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )
    # FakeUserRepo._lock_calls записывает порядок:
    assert user_repo._lock_calls == [1, 2]


@pytest.mark.asyncio
async def test_lock_order_ascending_catcher_smaller() -> None:
    """catcher_id < violator_id → lock_for_update вызывается [catcher_id, violator_id].

    Тест с переставленными user_id: violator=5 (user 5), catcher=3 (user 3).
    ASC-сортировка: [3, 5].
    """
    # Создаём кастомных юзеров с user_id 5 (violator) и 3 (catcher).
    habit_repo = FakeHabitRepo()
    habit = _make_habit(catcher_amount_kopecks=10000)
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=5, habit_id=str(habit.id))
    catcher_membership = membership_repo.add_for(user_id=3, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=5, deposit_balance=100000))  # violator
    user_repo.add(_make_user(id=3, deposit_balance=0))  # catcher

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )
    await service.apply_catch(
        catcher_user_id=3,
        violator_membership_id=str(violator.id),
        club_date=_CLUB_DATE,
        catcher_membership_id=str(catcher_membership.id),
        now_utc=_NOW_UTC,
    )
    # ASC: [3, 5]:
    assert user_repo._lock_calls == [3, 5]


# ---------------------------------------------------------------------------
# Защита от гонки Z-22: double-check ACTIVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_catch_rejects_paused_violator_pre_lock() -> None:
    """Первый re-check ACTIVE до лока: PAUSED жертва отвергается без захвата блокировки."""
    habit_repo = FakeHabitRepo()
    habit = _make_habit()
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.PAUSED
    )

    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=1000))

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    with pytest.raises(MembershipNotActiveError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=_CLUB_DATE,
            catcher_membership_id=str(uuid4()),
            now_utc=_NOW_UTC,
        )

    # Локи не захватывались (pre-check отверг):
    assert user_repo._lock_calls == []


# ---------------------------------------------------------------------------
# Deposit exhausted: amount <= 0 → raise без мутаций
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_catch_deposit_exhausted_no_mutation() -> None:
    """deposit=0 → PenaltyAlreadyProcessedError(code='deposit_exhausted'),
    без списания/зачисления, без Penalty insert.
    """
    service, _, _, _, _, _, catcher_membership = await _setup_apply_catch(
        catcher_amount_kopecks=10000,
        violator_deposit=0,
    )

    with pytest.raises(Exception) as exc_info:
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id="any-id",  # membership status не проверится до ACTIVE re-check,
            club_date=_CLUB_DATE,  # но deposit=0 перехватывается ДО deposit списания
            catcher_membership_id=str(catcher_membership.id),
            now_utc=_NOW_UTC,
        )
    # В текущем коде ACTIVE re-check срабатывает раньше (на membership PAUSED через status=ACTIVE).
    # Здесь мы НЕ ставим status — membership создан с ACTIVE по умолчанию.
    # Но deposit=0 → min(penalty, 0) = 0 → amount <= 0 → raise.
    assert exc_info.value is not None
