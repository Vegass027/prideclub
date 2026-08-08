"""Тесты MembershipService.join с проверкой депозита (Pravki-deposit-sse.md §Z-3.1).

Покрывает:
- Новый участник с deposit < penalty → InsufficientDepositError (403).
- Новый участник с deposit == penalty → успех (200 OK на API уровне).
- Новый участник с deposit > penalty → успех.
- Возобновление LEFT→ACTIVE — БЕЗ проверки депозита (даже если deposit=0).
- 3-клуб сценарий: catch в A обнулил deposit, join в D → 403.
- Sanity: required_kopecks/current_kopecks/club_penalty_kopecks в extras.
"""
from __future__ import annotations

import pytest

from app.core.constants import MembershipStatus
from app.core.exceptions import InsufficientDepositError
from app.models.user import User
from app.services.membership_service import MembershipService
from tests.fakes import (
    FakeHabitRepo,
    FakeMembershipRepo,
    FakeUserRepo,
    make_habit,
)


class _FakeSession:
    """Минимальный session — MembershipService.join использует .execute() для JOIN."""

    async def execute(self, stmt: object) -> object:
        class _R:
            def all(self_inner) -> list:
                return []

            def first(self_inner) -> object:
                return None

        return _R()


def _make_service(
    *,
    user_repo: FakeUserRepo,
    habit_repo: FakeHabitRepo,
    membership_repo: FakeMembershipRepo,
) -> MembershipService:
    return MembershipService(
        session=_FakeSession(),  # type: ignore[arg-type]
        membership_repo=membership_repo,  # type: ignore[arg-type]
        habit_repo=habit_repo,
        user_repo=user_repo,
    )


def _seed_user(user_repo: FakeUserRepo, *, user_id: int, deposit: int) -> None:
    user_repo.add(
        User(id=user_id, first_name=f"u{user_id}", deposit_balance=deposit)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_join_rejects_when_deposit_below_penalty() -> None:
    """deposit=0, penalty=500 → 403 InsufficientDepositError."""
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 500
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)

    service = _make_service(
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(InsufficientDepositError) as exc_info:
        await service.join(user_id=1, habit_id=str(habit.id))

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "insufficient_deposit"
    assert exc_info.value.extras == {
        "required_kopecks": 500,
        "current_kopecks": 0,
        "club_penalty_kopecks": 500,
    }


@pytest.mark.asyncio
async def test_join_succeeds_when_deposit_equals_penalty() -> None:
    """deposit=500, penalty=500 → ровно хватает, membership создан."""
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 500
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=500)

    service = _make_service(
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m = await service.join(user_id=1, habit_id=str(habit.id))
    assert m.status == MembershipStatus.ACTIVE
    assert m.user_id == 1
    assert m.habit_id == str(habit.id)


@pytest.mark.asyncio
async def test_join_succeeds_when_deposit_exceeds_penalty() -> None:
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 500
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=10_000)

    service = _make_service(
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    m = await service.join(user_id=1, habit_id=str(habit.id))
    assert m.status == MembershipStatus.ACTIVE


@pytest.mark.asyncio
async def test_left_to_active_requires_deposit() -> None:
    """Z-3.1 Variant B: LEFT→ACTIVE возобновление проверяет депозит.

    Старая версия (pre-этот-фикс) имела bug: deposit < penalty проходило
    без проверки через LEFT→ACTIVE bypass. Теперь deposit ВСЕГДА проверяется
    перед любой мутацией membership (Variant B, согласовано 2026-08-08).
    """
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 500
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    existing = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.LEFT
    )
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)  # deposit < penalty → 403

    service = _make_service(
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(InsufficientDepositError) as exc_info:
        await service.join(user_id=1, habit_id=str(habit.id))
    assert exc_info.value.code == "insufficient_deposit"
    # Статус membership НЕ изменился — остался LEFT.
    assert existing.status == MembershipStatus.LEFT

    # С достаточным депозитом — успешно реактивируется.
    _seed_user(user_repo, user_id=1, deposit=1000)
    m = await service.join(user_id=1, habit_id=str(habit.id))
    assert m.id == existing.id
    assert m.status == MembershipStatus.ACTIVE


@pytest.mark.asyncio
async def test_join_rejected_when_deposit_exhausted_by_other_club() -> None:
    """Сценарий из плана §Z-3.5: юзер в 3 клубах, catch в A обнулил deposit,
    join в новый клуб D → 403.
    """
    habit_repo = FakeHabitRepo()
    habit_d = make_habit()
    habit_d.penalty_amount = 500
    habit_repo.add(habit_d)
    membership_repo = FakeMembershipRepo()
    user_repo = FakeUserRepo()
    _seed_user(user_repo, user_id=1, deposit=0)  # catch в A обнулил

    service = _make_service(
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(InsufficientDepositError) as exc_info:
        await service.join(user_id=1, habit_id=str(habit_d.id))

    assert exc_info.value.extras["current_kopecks"] == 0
    assert exc_info.value.extras["required_kopecks"] == 500


@pytest.mark.asyncio
async def test_join_rejects_when_user_does_not_exist() -> None:
    """Edge case: юзер без записи в users — UserNotFoundError (404).

    Это крайне вырожденный кейс (TelegramUserDbDep делает upsert), но
    если всё-таки — план §Z-3.1 явно предписывает UserNotFoundError
    (НЕ MembershipNotFoundError), чтобы фронт мог различать «юзера нет»
    vs «клуба/мембершипа нет».
    """
    from app.core.exceptions import UserNotFoundError

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 500
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    user_repo = FakeUserRepo()  # без user

    service = _make_service(
        user_repo=user_repo,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
    )

    with pytest.raises(UserNotFoundError) as exc_info:
        await service.join(user_id=999, habit_id=str(habit.id))
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "user_not_found"
