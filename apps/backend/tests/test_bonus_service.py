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
from tests.fakes import FakeMembershipRepo


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

    penalty_by_id = {penalty.id: penalty}

    async def penalty_lookup(pid: str):
        return penalty_by_id.get(pid)

    async def user_lookup(uid: int):
        return user if uid == user.id else None

    membership_repo = FakeMembershipRepo()
    membership_repo.add(catcher_m)

    async def rule_lookup(*_args, **_kw):
        return None  # бонусных правил нет

    service = BonusService(
        session=None,  # type: ignore[arg-type]  # не используется при инъекции
        membership_repo=membership_repo,
        penalty_lookup=penalty_lookup,
        user_lookup=user_lookup,
        rule_lookup=rule_lookup,
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
