from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
    TransactionType,
)
from app.core.exceptions import (
    CannotCatchSelfError,
    PenaltyAlreadyProcessedError,
    TooManyCatchAttemptsError,
)
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.services.penalty_service import PenaltyService
from tests.fakes import (
    FakeHabitRepo,
    FakeMembershipRepo,
    FakeCheckinRepo,
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
        # PenaltyService делает SELECT по penalties для идемпотентности —
        # вернём пусто.
        class _Result:
            def first(self_inner) -> Any:
                return None

        return _Result()


class _NoopLimiter:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def incr_catch(self, catcher_user_id: int) -> int:
        self.calls.append(catcher_user_id)
        return 1


@pytest.mark.asyncio
async def test_apply_catch_happy_path() -> None:
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    violator.deposit_balance = 500
    checkin_repo = FakeCheckinRepo()
    limiter = _NoopLimiter()
    session = _NoStreakSession()

    async def no_suspicious(*_a, **_kw):
        return False

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        redis_port=limiter,
        suspicious_lookup=no_suspicious,
    )

    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
        catcher_membership_id=str(uuid4()),
    )
    assert penalty.amount == habit.penalty_amount
    assert violator.deposit_balance == 500 - habit.penalty_amount
    assert session.transactions[0].type == TransactionType.PENALTY.value
    assert session.transactions[0].balance_after == violator.deposit_balance


@pytest.mark.asyncio
async def test_apply_catch_cannot_catch_self() -> None:
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(user_id=1, habit_id=str(habit.id))

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
    )

    with pytest.raises(CannotCatchSelfError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(m.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(m.id),
        )


@pytest.mark.asyncio
async def test_apply_catch_deposit_exhausted_pauses_membership() -> None:
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 1000
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    violator.deposit_balance = 0  # депозит исчерпан — membership должен быть paused

    async def no_suspicious(*_a, **_kw):
        return False

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_lookup=no_suspicious,
    )

    with pytest.raises(PenaltyAlreadyProcessedError) as exc:
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(uuid4()),
        )
    # Депозит исчерпан → membership paused.
    assert violator.status == MembershipStatus.PAUSED


def test_rate_limit_parse() -> None:
    from app.services.penalty_service import _parse_limit

    assert _parse_limit("10/10s") == (10, 10)
    assert _parse_limit("5/1m") == (5, 60)


@pytest.mark.asyncio
async def test_penalty_full_amount_to_fund() -> None:
    """Принятая юр. модель: 100% штрафа → prize_pool (см. 01-concept §4)."""
    assert PenaltyConfig.FUND_SHARE == 1.0