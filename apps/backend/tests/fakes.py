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
        self._member_counts: dict[str, int] = {}

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

    async def list_active(self) -> list[Habit]:
        return [
            h for h in self._store.values()
            if h.is_active and h.archived_at is None
        ]

    async def iter_active(self):
        """Async generator — зеркалит прод-репозиторий для тестов стриминга."""
        for h in self._store.values():
            if h.is_active and h.archived_at is None:
                yield h

    async def list_including_archived(self) -> list[Habit]:
        return list(self._store.values())

    async def list_with_member_counts(self) -> list[tuple[Habit, int]]:
        return [
            (h, 0)
            for h in self._store.values()
            if h.is_active and h.archived_at is None
        ]

    async def list_for_user(self, user_id: int) -> list[Habit]:
        return [
            h for h in self._store.values()
            if h.is_active and h.archived_at is None
        ]

    async def add_to_prize_pool(self, habit_id: str, amount: int) -> None:
        habit = self._store.get(habit_id)
        if habit is None:
            return
        habit.prize_pool += amount

    async def lock_for_update(self, habit_id: str) -> Habit | None:
        """Зеркалит прод-репозиторий: SELECT ... FOR UPDATE на habit.
        В фейке блокировка не нужна — возвращаем объект как есть."""
        return self._store.get(habit_id)

    async def count_active_members(self, habit_id: str) -> int:
        """В фейке — статический счётчик, который тест может обновлять
        через `set_active_member_count(habit_id, n)`. В проде — COUNT из БД."""
        return self._member_counts.get(habit_id, 0)

    def set_active_member_count(self, habit_id: str, n: int) -> None:
        self._member_counts[habit_id] = n

    async def create(self, *, fields: dict) -> Habit:
        from datetime import time as _time

        defaults = {
            "id": str(uuid4()),
            "title": "",
            "chat_id": 0,
            "checkin_window_start": _time(0, 0),
            "checkin_window_end": _time(23, 59),
            "timezone": "Europe/Moscow",
            "penalty_amount": 0,
            "price_month": 0,
            "prize_pool": 0,
            "is_active": False,
            "proof_type": ProofType.VIDEO_NOTE,
        }
        merged = {**defaults, **fields}
        habit = Habit(**merged)
        self._store[str(habit.id)] = habit
        return habit

    async def update(self, habit: Habit, *, fields: dict) -> Habit:
        for k, v in fields.items():
            setattr(habit, k, v)
        return habit

    async def archive(self, habit: Habit, *, archived_at) -> None:
        habit.is_active = False
        habit.archived_at = archived_at

    async def restore(self, habit: Habit) -> None:
        habit.archived_at = None

    async def set_active(self, habit: Habit, *, is_active: bool) -> None:
        habit.is_active = is_active


class FakeSuspiciousPairsRepository:
    """Замена SuspiciousPairsRepository для unit-тестов PenaltyService.

    Тест задаёт множество пар через `flag(a, b)` (или напрямую редактирует
    `_pairs`); `lookup_flagged` возвращает True ровно для flagged-пар.
    """

    def __init__(self) -> None:
        self._pairs: set[tuple[str, str]] = set()

    @staticmethod
    def _canonical(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def flag(self, a: str, b: str) -> None:
        self._pairs.add(self._canonical(a, b))

    def clear(self, a: str, b: str) -> None:
        self._pairs.discard(self._canonical(a, b))

    async def lookup_flagged(self, a: str, b: str) -> bool:
        if a == b:
            return False
        return self._canonical(a, b) in self._pairs

    async def get(self, a: str, b: str):  # pragma: no cover
        return None

    async def ban(self, *, a: str, b: str, reason: str):  # pragma: no cover
        self._pairs.add(self._canonical(a, b))
        return None

    async def list_flagged(
        self,
        *,
        status: str | None = "flagged",
        limit: int = 100,
        offset: int = 0,
    ):  # pragma: no cover
        return []


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
        prize_pool=0,
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

    async def create(self, user_id: int, habit_id: str) -> Membership:
        """Зеркалит прод-сигнатуру: id генерируется в репо, не в сервисе."""
        m = Membership(
            id=str(uuid4()),
            user_id=user_id,
            habit_id=habit_id,
            status=MembershipStatus.ACTIVE,
            deposit_balance=0,
        )
        self._store[str(m.id)] = m
        return m

    async def iter_for_habit(self, habit_id: str):
        """Async generator — зеркалит прод-репозиторий для тестов стриминга."""
        for m in self._store.values():
            if str(m.habit_id) == habit_id:
                yield m


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