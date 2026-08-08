"""Тесты для MembershipService.recompute_pause_status (Pravki-deposit-sse.md §Z-2.5).

Покрываемые сценарии:
1. User без memberships — no-op.
2. User с deposit >= max(penalty) — все ACTIVE остаются ACTIVE, PAUSED остаются PAUSED.
3. User с deposit < одного из penalty — этот клуб становится PAUSED.
4. User с deposit < всех penalty — все ACTIVE → PAUSED.
5. После пополнения deposit — ранее PAUSED возвращается в ACTIVE.
6. LEFT membership НЕ трогается.
7. Параллельные клубы с разными penalty: deposit хватает на 2 из 3 → третий PAUSED.
8. User не существует — no-op, без exception.

Использует FakeUserRepo + FakeMembershipRepo + FakeHabitRepo — без зависимости от SQL.
"""
from __future__ import annotations

import pytest

from app.core.constants import MembershipStatus
from app.models.user import User
from app.services.membership_service import MembershipService
from tests.fakes import (
    FakeMembershipRepo,
    FakeUserRepo,
)


class _FakeSession:
    """Минимальный session — recompute_pause_status использует только .execute()."""

    def __init__(self, *, user: User | None = None) -> None:
        self._user = user
        self.executed: list[str] = []

    async def execute(self, stmt: object) -> object:
        self.executed.append(str(stmt))
        # Запрос Membership JOIN Habit в recompute_pause_status:
        # Возвращаем нужные memberships + penalty через глобальную ссылку на _ms_for_session.
        rows = []
        ms = _MembershipRepoHolder.repo_for_session.get(id(self))  # type: ignore[attr-defined]
        if ms is None:
            return _Result(rows=[])
        # Собираем (membership_id, status, penalty_amount) для всех не-LEFT.
        for m in ms._store.values():  # type: ignore[attr-defined]
            if m.user_id == _MembershipRepoHolder.user_id and m.status != MembershipStatus.LEFT:  # type: ignore[attr-defined]
                penalty = ms._habit_penalties.get(m.habit_id, 0)  # type: ignore[attr-defined]
                rows.append((m, penalty))
        return _Result(rows=rows)


class _Result:
    def __init__(self, *, rows: list | None = None, scalar: object = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list:
        return self._rows

    def scalar_one_or_none(self) -> object:
        return self._scalar


class _MembershipRepoHolder:
    """Глобальная ссылка для фейковой сессии — какой FakeMembershipRepo использовать."""

    repo_for_session: dict[int, FakeMembershipRepo] = {}
    user_id: int = 0


class _FakeMembershipRepoForRecompute(FakeMembershipRepo):
    """Расширенный фейк: хранит habit_id → penalty_amount для JOIN."""

    def __init__(self) -> None:
        super().__init__()
        self._habit_penalties: dict[str, int] = {}

    def set_penalty(self, habit_id: str, amount: int) -> None:
        self._habit_penalties[habit_id] = amount


def _make_service(
    user: User | None,
    ms_repo: _FakeMembershipRepoForRecompute,
    user_id: int,
) -> MembershipService:
    session = _FakeSession(user=user)
    _MembershipRepoHolder.repo_for_session[id(session)] = ms_repo
    _MembershipRepoHolder.user_id = user_id

    user_repo = FakeUserRepo()
    if user is not None:
        user_repo.add(user)

    return MembershipService(
        session=session,  # type: ignore[arg-type]
        membership_repo=ms_repo,  # type: ignore[arg-type]
        user_repo=user_repo,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_no_memberships_noop() -> None:
    """Юзер без memberships → ничего не делается, без exception."""
    user = User(id=1, first_name="u", deposit_balance=500)
    ms_repo = _FakeMembershipRepoForRecompute()
    service = _make_service(user, ms_repo, user_id=1)

    # Не падает — главное в этом тесте.
    await service.recompute_pause_status(1)
    # Никаких membership'ов — никаких мутаций.
    assert len(ms_repo._store) == 0


@pytest.mark.asyncio
async def test_recompute_missing_user_noop() -> None:
    """Юзер не существует → no-op (защита от вырожденного кейса)."""
    ms_repo = _FakeMembershipRepoForRecompute()
    service = _make_service(user=None, ms_repo=ms_repo, user_id=999)

    # Не падает.
    await service.recompute_pause_status(999)


@pytest.mark.asyncio
async def test_recompute_pauses_when_deposit_below_penalty() -> None:
    """deposit=500, penalty=1000 → ACTIVE → PAUSED."""
    user = User(id=1, first_name="u", deposit_balance=500)
    ms_repo = _FakeMembershipRepoForRecompute()
    m = ms_repo.add_for(user_id=1, habit_id="h1")
    ms_repo.set_penalty("h1", 1000)

    service = _make_service(user, ms_repo, user_id=1)
    await service.recompute_pause_status(1)

    assert m.status == MembershipStatus.PAUSED


@pytest.mark.asyncio
async def test_recompute_keeps_active_when_deposit_sufficient() -> None:
    """deposit=2000, penalty=1000 → ACTIVE остаётся ACTIVE."""
    user = User(id=1, first_name="u", deposit_balance=2000)
    ms_repo = _FakeMembershipRepoForRecompute()
    m = ms_repo.add_for(user_id=1, habit_id="h1")
    ms_repo.set_penalty("h1", 1000)

    service = _make_service(user, ms_repo, user_id=1)
    await service.recompute_pause_status(1)

    assert m.status == MembershipStatus.ACTIVE


@pytest.mark.asyncio
async def test_recompute_reactivates_paused_when_topped_up() -> None:
    """После пополнения: deposit >= penalty → PAUSED → ACTIVE."""
    user = User(id=1, first_name="u", deposit_balance=2000)
    ms_repo = _FakeMembershipRepoForRecompute()
    m = ms_repo.add_for(user_id=1, habit_id="h1", status=MembershipStatus.PAUSED)
    ms_repo.set_penalty("h1", 1000)

    service = _make_service(user, ms_repo, user_id=1)
    await service.recompute_pause_status(1)

    assert m.status == MembershipStatus.ACTIVE


@pytest.mark.asyncio
async def test_recompute_skips_left_memberships() -> None:
    """LEFT memberships НЕ трогаем — это явное действие юзера."""
    user = User(id=1, first_name="u", deposit_balance=0)
    ms_repo = _FakeMembershipRepoForRecompute()
    m_left = ms_repo.add_for(user_id=1, habit_id="h1", status=MembershipStatus.LEFT)
    m_active = ms_repo.add_for(user_id=1, habit_id="h2")
    ms_repo.set_penalty("h1", 1000)
    ms_repo.set_penalty("h2", 1000)

    service = _make_service(user, ms_repo, user_id=1)
    await service.recompute_pause_status(1)

    assert m_left.status == MembershipStatus.LEFT  # не тронут
    assert m_active.status == MembershipStatus.PAUSED  # депозит < penalty


@pytest.mark.asyncio
async def test_recompute_mixed_clubs_selective_pause() -> None:
    """Параллельные клубы с разными penalty: депозит хватает на часть.

    Сценарий: deposit=2500, club A penalty=1000, club B penalty=1000,
    club C penalty=5000. Ожидаемо:
    - A, B остаются ACTIVE (2500 >= 1000).
    - C становится PAUSED (2500 < 5000).
    """
    user = User(id=1, first_name="u", deposit_balance=2500)
    ms_repo = _FakeMembershipRepoForRecompute()
    m_a = ms_repo.add_for(user_id=1, habit_id="A")
    m_b = ms_repo.add_for(user_id=1, habit_id="B")
    m_c = ms_repo.add_for(user_id=1, habit_id="C")
    ms_repo.set_penalty("A", 1000)
    ms_repo.set_penalty("B", 1000)
    ms_repo.set_penalty("C", 5000)

    service = _make_service(user, ms_repo, user_id=1)
    await service.recompute_pause_status(1)

    assert m_a.status == MembershipStatus.ACTIVE
    assert m_b.status == MembershipStatus.ACTIVE
    assert m_c.status == MembershipStatus.PAUSED


@pytest.mark.asyncio
async def test_recompute_does_not_touch_other_users_memberships() -> None:
    """Не трогаем memberships других юзеров."""
    user = User(id=1, first_name="u", deposit_balance=0)
    ms_repo = _FakeMembershipRepoForRecompute()
    my_m = ms_repo.add_for(user_id=1, habit_id="h1")
    other_m = ms_repo.add_for(user_id=2, habit_id="h1")
    ms_repo.set_penalty("h1", 1000)

    service = _make_service(user, ms_repo, user_id=1)
    await service.recompute_pause_status(1)

    assert my_m.status == MembershipStatus.PAUSED  # мой membership заморожен
    assert other_m.status == MembershipStatus.ACTIVE  # чужой membership не тронут
