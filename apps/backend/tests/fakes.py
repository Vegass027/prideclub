from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

from app.core.constants import (
    CheckinStatus,
    MembershipStatus,
    PenaltyReason,
    ProofType,
)
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.models.user import User


class FakeUserRepo:
    def __init__(self) -> None:
        self._store: dict[int, User] = {}

    async def upsert(self, *, id: int, first_name: str, username: str | None) -> User:
        u = User(id=id, first_name=first_name, username=username)
        self._store[id] = u
        return u

    async def get(self, user_id: int) -> User | None:
        return self._store.get(user_id)


class FakeHabitRepo:
    def __init__(self) -> None:
        self._store: dict[str, Habit] = {}

    def add(self, habit: Habit) -> Habit:
        self._store[str(habit.id)] = habit
        return habit

    async def get(self, habit_id: str) -> Habit | None:
        return self._store.get(habit_id)

    async def get_by_chat_id(self, chat_id: int) -> Habit | None:
        for h in self._store.values():
            if h.chat_id == chat_id:
                return h
        return None

    async def list_with_member_counts(self) -> list[tuple[Habit, int]]:
        return [(h, 0) for h in self._store.values()]


def make_habit(
    *, id: str | None = None, chat_id: int = 100, proof: ProofType = ProofType.VIDEO_NOTE
) -> Habit:
    return Habit(
        id=id or str(uuid4()),
        title="Test Habit",
        chat_id=chat_id,
        checkin_window_start=time(0, 0),
        checkin_window_end=time(23, 59),
        timezone="Europe/Moscow",
        penalty_amount=100,
        price_month=1000,
        proof_type=proof,
    )


class FakeMembershipRepo:
    def __init__(self) -> None:
        self._store: dict[str, Membership] = {}

    def add(self, m: Membership) -> Membership:
        self._store[str(m.id)] = m
        return m

    def add_for(
        self, *, user_id: int, habit_id: str, status: MembershipStatus = MembershipStatus.ACTIVE
    ) -> Membership:
        m = Membership(
            id=str(uuid4()),
            user_id=user_id,
            habit_id=habit_id,
            status=status,
            deposit_balance=1000,
        )
        self._store[str(m.id)] = m
        return m

    async def get(self, membership_id: str) -> Membership | None:
        return self._store.get(membership_id)

    async def get_for_user_in_habit(
        self, user_id: int, habit_id: str
    ) -> Membership | None:
        for m in self._store.values():
            if m.user_id == user_id and m.habit_id == habit_id:
                return m
        return None

    async def lock_for_update(self, membership_id: str) -> Membership:
        m = self._store.get(membership_id)
        if m is None:
            raise KeyError(membership_id)
        return m


class FakeCheckinRepo:
    def __init__(self) -> None:
        self._store: dict[tuple[str, date], Checkin] = {}

    async def get_for_date(self, membership_id: str, on_date: date) -> Checkin | None:
        return self._store.get((membership_id, on_date))

    async def get_or_create_done(
        self, *, membership_id: str, on_date: date, proof_message_id: int
    ) -> tuple[Checkin, bool]:
        existing = self._store.get((membership_id, on_date))
        if existing is not None:
            return existing, False
        c = Checkin(
            id=str(uuid4()),
            membership_id=membership_id,
            date=on_date,
            status=CheckinStatus.DONE,
            proof_message_id=proof_message_id,
        )
        self._store[(membership_id, on_date)] = c
        return c, True


class FakeCache:
    def __init__(self) -> None:
        self.invalidated: list[tuple[str, str]] = []

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None:
        self.invalidated.append((habit_id, membership_id))


class FakeSession:
    """Минимальный session для SELECT-эмиттирующих методов сервиса streak."""

    def __init__(self, checkin_repo: FakeCheckinRepo) -> None:
        self._checkin_repo = checkin_repo
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        # сервис делает один SELECT streak — эмулируем через FakeCheckinRepo.
        from sqlalchemy import select

        from app.models.checkin import Checkin

        if isinstance(stmt, select(Checkin).where(Checkin.membership_id == None).__class__):
            pass

        # Загружаем стрик из FakeCheckinRepo, фильтруя по ключу.
        dates: list[date] = []
        for (m_id, d), c in self._checkin_repo._store.items():
            if c.status == CheckinStatus.DONE:
                dates.append(d)
        dates.sort(reverse=True)

        class _Result:
            def all(self_inner) -> list[tuple[date]]:
                return [(d,) for d in dates]

        return _Result()