from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.constants import (
    CheckinStatus,
    MembershipStatus,
    ProofType,
)
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User


class FakeUserRepo:
    """Замена UserRepository. Тест задаёт пользователей через `add(user)`.

    Покрывает контракт:
    - get(user_id) — T3 BonusService
    - lock_for_update(user_id) — PenaltyService/PaymentService (PR #1, Z-2.4)
    - add_balance(user_id, amount) — утилита для тестов recompute_pause_status

    В фейке блокировка не нужна — возвращаем объект как есть.
    """

    def __init__(self) -> None:
        self._store: dict[int, User] = {}
        self._lock_calls: list[int] = []

    def add(self, user: User) -> None:
        self._store[user.id] = user

    async def get(self, user_id: int) -> User | None:
        return self._store.get(user_id)

    async def lock_for_update(self, user_id: int) -> User | None:
        """Фейк SELECT FOR UPDATE — в фейке блокировка no-op."""
        self._lock_calls.append(user_id)
        return self._store.get(user_id)

    async def add_balance(self, user_id: int, amount: int) -> User:
        """Фейк: lock_for_update + deposit_balance += amount."""
        u = await self.lock_for_update(user_id)
        if u is None:
            raise ValueError(f"user {user_id} not found")
        u.deposit_balance += amount
        return u


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

    async def get_by_chat_and_thread(
        self, chat_id: int, message_thread_id: int
    ) -> Habit | None:
        for h in self._store.values():
            if h.chat_id == chat_id and h.checkin_topic_thread_id == message_thread_id:
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
            "proof_types": [ProofType.VIDEO_NOTE.value],
        }
        merged = {**defaults, **fields}
        # Синхронизируем proof_type с первым из proof_types если нужно.
        if "proof_types" in merged and merged["proof_types"]:
            merged["proof_type"] = ProofType(merged["proof_types"][0])
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
    *,
    id: str | None = None,
    chat_id: int = 100,
    proof: ProofType = ProofType.VIDEO_NOTE,
    proof_types: list[str] | None = None,
) -> Habit:
    pts = proof_types if proof_types is not None else [proof.value]
    return Habit(
        id=id or str(uuid4()),
        title="Test Habit",
        chat_id=chat_id,
        # Pravki-manual-catch-2026-08-18 §Шаг 2: окно 09:00-21:00 MSK
        # имеет реальный catch window (21:00 → 09:00 next day - 2h = 04:00 UTC
        # next day). Старое 00:00-23:59 давало пустой catch window (1 минута
        # между окнами, минус 2h buffer).
        checkin_window_start=time(9, 0),
        checkin_window_end=time(21, 0),
        timezone="Europe/Moscow",
        penalty_amount=100,
        price_month=1000,
        prize_pool=0,
        proof_type=proof,
        proof_types=pts,
        # Pravki-catcher-deposit (Phase 1 Task 1.1, 2026-08-21): default=0 в
        # SQLAlchemy mapped_column не применяется в Python __init__ если
        # значение не передано — поэтому явно передаём 0 для Fake-тестов
        # (старое поведение "всё в фонд" по умолчанию).
        catcher_amount_kopecks=0,
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
        # Pravki-deposit-sse.md §Z-2.1: deposit_balance больше не на membership.
        m = Membership(
            id=str(uuid4()),
            user_id=user_id,
            habit_id=habit_id,
            status=status,
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
        # Pravki-deposit-sse.md §Z-2.1: deposit_balance больше не на membership.
        m = Membership(
            id=str(uuid4()),
            user_id=user_id,
            habit_id=habit_id,
            status=MembershipStatus.ACTIVE,
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

    async def get_recent_dates(
        self,
        membership_id: str,
        up_to,
        *,
        limit: int = 90,
    ) -> list[date]:
        """Зеркалит прод-репозиторий: только DONE-чекины, ≤ up_to, по убыванию."""
        out: list[date] = []
        for (m_id, d), c in self._store.items():
            if m_id != membership_id:
                continue
            if d > up_to:
                continue
            if c.status != CheckinStatus.DONE:
                continue
            out.append(d)
        out.sort(reverse=True)
        return out[:limit]

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

    async def upsert_status(
        self,
        *,
        membership_id: str,
        on_date: date,
        status: CheckinStatus,
    ) -> Checkin:
        """Зеркалит прод-метод: INSERT или UPDATE status существующего Checkin.

        Pravki-bug-fixes §Z-21: разрешает transitions
        pending → caught, pending → missed, missed → caught.
        proof_message_id сохраняется если был (НЕ перезаписывается).
        """
        key = (membership_id, on_date)
        existing = self._store.get(key)
        if existing is None:
            c = Checkin(
                id=str(uuid4()),
                membership_id=membership_id,
                date=on_date,
                status=status,
                proof_message_id=None,
            )
        else:
            existing.status = status
            c = existing
        self._store[key] = c
        return c

    async def count_done_for_memberships(
        self, membership_ids: list[str]
    ) -> dict[str, int]:
        counts: dict[str, int] = {mid: 0 for mid in membership_ids}
        for (m_id, _d), c in self._store.items():
            if c.status != CheckinStatus.DONE:
                continue
            if m_id in counts:
                counts[m_id] += 1
        return counts

    async def count_done_for_membership(self, membership_id: str) -> int:
        result = await self.count_done_for_memberships([membership_id])
        return result.get(membership_id, 0)


class FakeCache:
    def __init__(self) -> None:
        self.invalidated: list[tuple[str, str]] = []

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None:
        self.invalidated.append((habit_id, membership_id))


class FakeAvatarService:
    """Замена AvatarService для unit-тестов.

    По умолчанию get_or_fetch_local_path возвращает None (404).
    Тест может подложить fake JPEG-файл через `set_local_path` или
    переопределить `get_or_fetch_local_path` напрямую.
    """

    def __init__(self) -> None:
        self._local_paths: dict[int, Path] = {}

    def set_local_path(self, user_id: int, path: Path) -> None:
        self._local_paths[user_id] = path

    async def get_or_fetch_local_path(
        self, user_id: int, file_id: str | None
    ) -> Path | None:
        if not file_id:
            return None
        return self._local_paths.get(user_id)

    async def get_cdn_url(
        self, user_id: int, file_id: str | None
    ) -> str | None:
        return None


class FakePenaltyRepo:
    """Замена PenaltyRepository для unit-тестов.

    Тест наполняет через `add()`; lookup `get(penalty_id)` возвращает
    объект Penalty или None. totals_for_membership* считает по self._store
    (имитирует реальную агрегацию).
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def add(self, penalty: Penalty) -> None:
        self._store[str(penalty.id)] = penalty

    async def get(self, penalty_id: str) -> Penalty | None:
        return self._store.get(penalty_id)

    async def totals_for_memberships(
        self,
        membership_ids: list[str],
        *,
        as_violator: bool = True,
    ) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for mid in membership_ids:
            cnt = 0
            total = 0
            for p in self._store.values():
                if as_violator and str(p.membership_id) == mid:
                    cnt += 1
                    total += int(p.amount)
                elif not as_violator and str(p.catcher_membership_id) == mid:
                    cnt += 1
                    total += int(p.amount)
            result[mid] = (cnt, total)
        return result

    async def totals_for_membership(
        self,
        membership_id: str,
        *,
        as_violator: bool = True,
    ) -> tuple[int, int]:
        result = await self.totals_for_memberships(
            [membership_id], as_violator=as_violator
        )
        return result.get(membership_id, (0, 0))

    async def ids_with_any_penalty_today(
        self,
        *,
        membership_ids: list[str],
        club_date,
    ) -> set[str]:
        """Зеркалит прод-метод: возвращает {membership_id} для которых есть
        Penalty за club_date (любого reason).
        Pravki-bug-fixes §Z-21 (can_catch fix)."""
        if not membership_ids:
            return set()
        out: set[str] = set()
        mid_set = {str(m) for m in membership_ids}
        for p in self._store.values():
            if str(p.membership_id) in mid_set and p.date == club_date:
                out.add(str(p.membership_id))
        return out

    async def has_any_penalty_today(
        self,
        *,
        membership_id: str,
        club_date,
    ) -> bool:
        return membership_id in await self.ids_with_any_penalty_today(
            membership_ids=[membership_id],
            club_date=club_date,
        )

    async def amount_for_today(
        self,
        *,
        membership_id: str,
        club_date,
    ) -> int:
        """Сумма Penalty за club_date для одного membership.

        Pravki-paused-window-open-2026-08-14: новый метод в PenaltyRepository,
        нужен для условного рендера в TodayPage ("штраф списан" только
        если penalty_for_today_kopecks > 0).
        """
        total = 0
        for p in self._store.values():
            if str(p.membership_id) == str(membership_id) and p.date == club_date:
                total += int(p.amount)
        return total


class FakeBonusRuleRepo:
    """Замена BonusRuleRepository. Тест задаёт правила через `set(event, threshold, rule)`."""

    def __init__(self) -> None:
        self._rules: dict[tuple[str, int], Any] = {}

    def set(self, event_type: str, threshold: int, rule: Any) -> None:
        self._rules[(event_type, threshold)] = rule

    async def find(self, event_type: str, *, threshold: int) -> Any:
        return self._rules.get((event_type, threshold))


class FakeSession:
    """Минимальный session для CheckinService.process_checkin.

    До T4 — нужен был execute() для стрика. После T4 SELECT ушёл в
    FakeCheckinRepo.get_recent_dates, и FakeSession нужен только ради
    add/commit/rollback/flush, чтобы сервис мог собраться.
    """

    def __init__(self, checkin_repo: FakeCheckinRepo) -> None:
        self._checkin_repo = checkin_repo
        self.committed = False
        self.flushed = 0

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def flush(self) -> None:
        self.flushed += 1

    async def execute(self, stmt: Any) -> Any:
        raise NotImplementedError(
            "After T4 CheckinService no longer SELECTs via session.execute; "
            "streak dates come via CheckinRepository.get_recent_dates()."
        )