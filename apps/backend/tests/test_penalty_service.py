from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    TransactionType,
)
from app.core.exceptions import (
    CannotCatchSelfError,
    PenaltyAlreadyProcessedError,
)
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.services.penalty_service import PenaltyService
from tests.fakes import (
    FakeCheckinRepo,
    FakeHabitRepo,
    FakeMembershipRepo,
    FakeSuspiciousPairsRepository,
    FakeUserRepo,
    make_habit,
)


class _NoStreakSession:
    """Достаточно для SELECT-чеков и add()-операций PenaltyService."""

    def __init__(self) -> None:
        self.penalties: list[Penalty] = []
        self.transactions: list[Transaction] = []
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    def add(self, obj: Any) -> None:
        if isinstance(obj, Penalty):
            self.penalties.append(obj)
        elif isinstance(obj, Transaction):
            self.transactions.append(obj)

    async def flush(self) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        # PenaltyService делает SELECT по penalties для идемпотентности
        # и MembershipService.recompute_pause_status делает JOIN-запрос —
        # вернём пусто для обоих.
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


@pytest.mark.asyncio
async def test_apply_catch_happy_path() -> None:
    """Pravki-deposit-sse.md §Z-2: депозит списывается с user, не с membership."""
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    checkin_repo = FakeCheckinRepo()
    limiter = _NoopLimiter()
    session = _NoStreakSession()

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=limiter,
    )

    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
        catcher_membership_id=str(uuid4()),
    )
    assert penalty.amount == habit.penalty_amount
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500 - habit.penalty_amount
    assert session.transactions[0].type == TransactionType.PENALTY.value
    assert session.transactions[0].balance_after == violator_user.deposit_balance


@pytest.mark.asyncio
async def test_apply_catch_cannot_catch_self() -> None:
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    with pytest.raises(CannotCatchSelfError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(m.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(m.id),
        )


@pytest.mark.asyncio
async def test_apply_catch_deposit_exhausted_raises_without_mutation() -> None:
    """deposit=0 → PenaltyService бросает PenaltyAlreadyProcessedError
    с code="deposit_exhausted", НЕ мутируя ни deposit, ни status.

    По Pravki правке B единственный источник статуса — recompute_pause_status,
    и worker (apps/worker/worker/tasks/process_penalty.py:_pause_violator)
    вызывает его в отдельной транзакции при получении кода "deposit_exhausted".
    Здесь мы проверяем контракт PenaltyService: raise + правильный код,
    без in-mutation (rollback транзакции всё равно откатит любую мутацию).
    """
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 1000
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    with pytest.raises(PenaltyAlreadyProcessedError) as exc_info:
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(uuid4()),
        )
    assert exc_info.value.code == "deposit_exhausted"

    # PenaltyService НЕ мутирует status (rollback всё равно бы стёр мутацию).
    # Worker вызовет recompute_pause_status отдельной транзакцией.
    assert violator.status == MembershipStatus.ACTIVE
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 0  # не ушёл в минус


def test_rate_limit_parse() -> None:
    # Источник правды: app.core.utils.parse_rate_limit_spec (T1: дедупликация).
    from app.core.utils import parse_rate_limit_spec

    assert parse_rate_limit_spec("10/10s") == (10, 10)
    assert parse_rate_limit_spec("5/1m") == (5, 60)


@pytest.mark.asyncio
async def test_penalty_full_amount_to_fund() -> None:
    """Принятая юр. модель: 100% штрафа → prize_pool (см. 01-concept §4)."""
    assert PenaltyConfig.FUND_SHARE == 1.0


def _make_user(*, id: int, deposit_balance: int) -> Any:
    """Хелпер для теста — не подтягиваем models.user.Umporary, чтобы избежать
    зависимости от полной БД-модели User (с photo_file_id и т.п.).
    """
    from app.models.user import User

    return User(
        id=id,
        first_name=f"u{id}",
        deposit_balance=deposit_balance,
    )
