from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
)
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User
from app.services.bonus_service import BonusService
from tests.fakes import (
    FakeBonusRuleRepo,
    FakeMembershipRepo,
    FakePenaltyRepo,
    FakeSuspiciousPairsRepository,
    FakeUserRepo,
)


class _FakeSession:
    """Достаточно для INSERT/SELECT-минимума BonusService."""

    def __init__(self) -> None:
        self.transactions: list = []
        self.commits = 0
        self.flushes = 0

    def add(self, obj: object) -> None:
        self.transactions.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass

    async def flush(self) -> None:
        self.flushes += 1


@pytest.mark.asyncio
async def test_apply_catch_bonus_idempotent() -> None:
    user = User(id=1, first_name="cat", bonus_points=0)
    catcher_m = Membership(
        id=str(uuid4()),
        user_id=user.id,
        habit_id=str(uuid4()),
        status=MembershipStatus.ACTIVE,
    )
    penalty = Penalty(
        id=str(uuid4()),
        membership_id=str(uuid4()),
        catcher_membership_id=str(catcher_m.id),
        amount=100,
        fund_share=100,
        reason=PenaltyReason.CAUGHT,
        date=date(2026, 1, 1),
    )

    penalty_repo = FakePenaltyRepo()
    penalty_repo.add(penalty)
    user_repo = FakeUserRepo()
    user_repo.add(user)
    membership_repo = FakeMembershipRepo()
    membership_repo.add(catcher_m)

    service = BonusService(
        session=_FakeSession(),  # type: ignore[arg-type]
        membership_repo=membership_repo,
        penalty_repo=penalty_repo,
        user_repo=user_repo,
        bonus_rule_repo=FakeBonusRuleRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
    )

    # Первый вызов — начисляет +1, помечает penalty.bonus_applied.
    applied = await service.apply_catch_bonus(
        catcher_membership_id=str(catcher_m.id),
        penalty_id=str(penalty.id),
    )
    assert applied == PenaltyConfig.CATCHER_BONUS_POINTS
    assert penalty.bonus_applied is True
    assert user.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS

    # Повторный вызов с тем же penalty_id — идемпотентно: 0 поинтов, состояние не меняется.
    applied_again = await service.apply_catch_bonus(
        catcher_membership_id=str(catcher_m.id),
        penalty_id=str(penalty.id),
    )
    assert applied_again == 0
    assert user.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS


@pytest.mark.asyncio
async def test_apply_catch_bonus_skips_already_applied() -> None:
    user = User(id=2, first_name="u", bonus_points=10)
    catcher_m = Membership(
        id=str(uuid4()),
        user_id=user.id,
        habit_id=str(uuid4()),
        status=MembershipStatus.ACTIVE,
    )
    penalty = Penalty(
        id=str(uuid4()),
        membership_id=str(uuid4()),
        catcher_membership_id=str(catcher_m.id),
        amount=100,
        fund_share=100,
        reason=PenaltyReason.CAUGHT,
        date=date(2026, 1, 1),
        bonus_applied=True,  # уже начислено
    )

    penalty_repo = FakePenaltyRepo()
    penalty_repo.add(penalty)
    user_repo = FakeUserRepo()
    user_repo.add(user)
    membership_repo = FakeMembershipRepo()
    membership_repo.add(catcher_m)

    service = BonusService(
        session=_FakeSession(),  # type: ignore[arg-type]
        membership_repo=membership_repo,
        penalty_repo=penalty_repo,
        user_repo=user_repo,
        bonus_rule_repo=FakeBonusRuleRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
    )

    applied = await service.apply_catch_bonus(
        catcher_membership_id=str(catcher_m.id),
        penalty_id=str(penalty.id),
    )
    assert applied == 0
    assert user.bonus_points == 10  # неизменно


@pytest.mark.asyncio
async def test_apply_catch_bonus_skips_suspicious_pair() -> None:
    user = User(id=3, first_name="u", bonus_points=0)
    catcher_m = Membership(
        id=str(uuid4()),
        user_id=user.id,
        habit_id=str(uuid4()),
        status=MembershipStatus.ACTIVE,
    )
    violator_m_id = str(uuid4())
    penalty = Penalty(
        id=str(uuid4()),
        membership_id=violator_m_id,
        catcher_membership_id=str(catcher_m.id),
        amount=100,
        fund_share=100,
        reason=PenaltyReason.CAUGHT,
        date=date(2026, 1, 1),
        bonus_applied=False,
    )

    penalty_repo = FakePenaltyRepo()
    penalty_repo.add(penalty)
    user_repo = FakeUserRepo()
    user_repo.add(user)
    membership_repo = FakeMembershipRepo()
    membership_repo.add(catcher_m)

    suspicious_repo = FakeSuspiciousPairsRepository()
    suspicious_repo.flag(str(catcher_m.id), violator_m_id)

    service = BonusService(
        session=_FakeSession(),  # type: ignore[arg-type]
        membership_repo=membership_repo,
        penalty_repo=penalty_repo,
        user_repo=user_repo,
        bonus_rule_repo=FakeBonusRuleRepo(),
        suspicious_repo=suspicious_repo,
    )

    applied = await service.apply_catch_bonus(
        catcher_membership_id=str(catcher_m.id),
        penalty_id=str(penalty.id),
    )
    assert applied == 0
    assert user.bonus_points == 0
    assert penalty.bonus_applied is False  # НЕ помечаем, потому что заблокировано


@pytest.mark.asyncio
async def test_apply_catch_bonus_no_catcher_returns_zero() -> None:
    """Если у penalty.catcher_membership_id = None (window_closed_no_catch) — бонуса нет."""
    user = User(id=4, first_name="u", bonus_points=0)
    penalty = Penalty(
        id=str(uuid4()),
        membership_id=str(uuid4()),
        catcher_membership_id=None,  # нет ловца
        amount=100,
        fund_share=100,
        reason=PenaltyReason.WINDOW_CLOSED_NO_CATCH,
        date=date(2026, 1, 1),
        bonus_applied=False,
    )

    penalty_repo = FakePenaltyRepo()
    penalty_repo.add(penalty)
    user_repo = FakeUserRepo()
    user_repo.add(user)
    membership_repo = FakeMembershipRepo()

    service = BonusService(
        session=_FakeSession(),  # type: ignore[arg-type]
        membership_repo=membership_repo,
        penalty_repo=penalty_repo,
        user_repo=user_repo,
        bonus_rule_repo=FakeBonusRuleRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
    )

    applied = await service.apply_catch_bonus(
        catcher_membership_id="ignored",
        penalty_id=str(penalty.id),
    )
    assert applied == 0
    assert user.bonus_points == 0
    assert penalty.bonus_applied is False
