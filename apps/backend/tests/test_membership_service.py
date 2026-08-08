"""Тесты для MembershipService.join: проверка member_limit.

Покрываемые сценарии (TZ §2.1: member_limit — лимит участников, NULL = без лимита):
1. member_limit = NULL — лимит не проверяется.
2. member_limit = N, активных < N — вступление успешно.
3. member_limit = N, активных = N — 409 HabitMemberLimitReachedError.
4. LEFT → ACTIVE (возобновление) — лимит не применяется.

PR #2: deposit-проверка тоже срабатывает в join(), поэтому каждый
тест сидит user'а с достаточным deposit_balance (через FakeUserRepo).
"""
from __future__ import annotations

import pytest

from app.core.constants import MembershipStatus
from app.core.exceptions import HabitMemberLimitReachedError
from app.models.user import User
from app.services.membership_service import MembershipService
from tests.fakes import FakeHabitRepo, FakeMembershipRepo, FakeUserRepo, make_habit


def _service(
    habit_repo: FakeHabitRepo,
    membership_repo: FakeMembershipRepo,
    user_repo: FakeUserRepo,
) -> MembershipService:
    return MembershipService(
        session=None,  # fake repo не использует session
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        user_repo=user_repo,
    )


def _seed_user(user_repo: FakeUserRepo, *, user_id: int, deposit: int = 10_000) -> None:
    """Достаточно deposit для входа в любой habit из тестов."""
    user_repo.add(User(id=user_id, first_name=f"u{user_id}", deposit_balance=deposit))


@pytest.mark.asyncio
async def test_join_no_member_limit_always_allowed() -> None:
    habit = make_habit()  # member_limit по умолчанию None в make_habit
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1)
    service = _service(habit_repo, membership_repo, user_repo)

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
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1)
    service = _service(habit_repo, membership_repo, user_repo)

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
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=42)
    service = _service(habit_repo, membership_repo, user_repo)

    with pytest.raises(HabitMemberLimitReachedError):
        await service.join(user_id=42, habit_id=str(habit.id))


@pytest.mark.asyncio
async def test_rejoin_left_member_bypasses_member_limit() -> None:
    """Возобновление после LEFT — лимит не применяется (бывший член имеет приоритет).

    Иначе бы нельзя было вернуться в клуб после выхода, даже если место освободилось.
    PR #2: для LEFT→ACTIVE депозит тоже НЕ проверяется (даже если 0).
    """
    habit = make_habit()
    habit.member_limit = 1
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    habit_repo.set_active_member_count(str(habit.id), 1)  # лимит заполнен

    membership_repo = FakeMembershipRepo()
    prev = membership_repo.add_for(
        user_id=7, habit_id=str(habit.id), status=MembershipStatus.LEFT
    )

    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=7, deposit=0)  # deposit=0 но LEFT→ACTIVE ОК

    service = _service(habit_repo, membership_repo, user_repo)
    result = await service.join(user_id=7, habit_id=str(habit.id))

    assert result.id == prev.id
    assert result.status == MembershipStatus.ACTIVE
