"""Тесты для MembershipService.join: проверка member_limit.

Покрываемые сценарии (TZ §2.1: member_limit — лимит участников, NULL = без лимита):
1. member_limit = NULL — лимит не проверяется.
2. member_limit = N, активных < N — вступление успешно.
3. member_limit = N, активных = N — 409 HabitMemberLimitReachedError.
4. LEFT → ACTIVE (возобновление) — лимит не применяется.
"""
from __future__ import annotations

import pytest

from app.core.exceptions import HabitMemberLimitReachedError
from app.services.membership_service import MembershipService
from tests.fakes import FakeHabitRepo, FakeMembershipRepo, make_habit


def _service(habit_repo: FakeHabitRepo, membership_repo: FakeMembershipRepo) -> MembershipService:
    return MembershipService(
        session=None,  # membership_service не использует session напрямую
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )


@pytest.mark.asyncio
async def test_join_no_member_limit_always_allowed() -> None:
    habit = make_habit()  # member_limit по умолчанию None в make_habit
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    service = _service(habit_repo, membership_repo)

    m = await service.join(user_id=1, habit_id=str(habit.id))

    assert m.status.value == "active"
    assert m.user_id == 1


@pytest.mark.asyncio
async def test_join_below_member_limit_allowed() -> None:
    habit = make_habit()
    habit.member_limit = 5
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    habit_repo.set_active_member_count(str(habit.id), 4)  # один слот свободен
    membership_repo = FakeMembershipRepo()
    service = _service(habit_repo, membership_repo)

    m = await service.join(user_id=1, habit_id=str(habit.id))

    assert m.user_id == 1


@pytest.mark.asyncio
async def test_join_at_member_limit_rejected() -> None:
    """Граница: активных ровно столько же, сколько лимит — вступление невозможно."""
    habit = make_habit()
    habit.member_limit = 3
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    habit_repo.set_active_member_count(str(habit.id), 3)  # лимит исчерпан
    membership_repo = FakeMembershipRepo()
    service = _service(habit_repo, membership_repo)

    with pytest.raises(HabitMemberLimitReachedError):
        await service.join(user_id=42, habit_id=str(habit.id))


@pytest.mark.asyncio
async def test_rejoin_left_member_bypasses_member_limit() -> None:
    """Возобновление после LEFT — лимит не применяется (бывший член имеет приоритет).

    Иначе бы нельзя было вернуться в клуб после выхода, даже если место освободилось.
    """
    from app.core.constants import MembershipStatus

    habit = make_habit()
    habit.member_limit = 1
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    habit_repo.set_active_member_count(str(habit.id), 1)  # лимит заполнен

    membership_repo = FakeMembershipRepo()
    prev = membership_repo.add_for(
        user_id=7, habit_id=str(habit.id), status=MembershipStatus.LEFT
    )

    service = _service(habit_repo, membership_repo)
    result = await service.join(user_id=7, habit_id=str(habit.id))

    assert result.id == prev.id
    assert result.status == MembershipStatus.ACTIVE
