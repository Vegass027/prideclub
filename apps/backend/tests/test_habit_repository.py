"""Тесты для HabitRepository: контракт FOR UPDATE.

Проверяем, что:
1. add_to_prize_pool вызывает session.get с with_for_update=True.
2. Идемпотентный повтор корректно накапливает значение.
3. Если клуб не найден — no-op без raise.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.repositories.habit_repository import HabitRepository
from tests.fakes import make_habit


class _LockTrackingSession:
    """Минимальная async-сессия, которая фиксирует вызовы get/for_update.

    Достаточно для unit-теста контракта блокировки — реальный Postgres
    поведение подтверждается интеграционно на проде.
    """

    def __init__(self, store: dict) -> None:
        self._store = store
        self.lock_calls: list[tuple[Any, Any, dict]] = []

    async def get(
        self,
        entity: Any,
        ident: Any,
        *,
        with_for_update: Any = None,
        populate_existing: bool = False,
    ) -> Any:
        self.lock_calls.append((entity, ident, {"with_for_update": with_for_update}))
        return self._store.get(str(ident)) if str(ident) in self._store else None

    # Не используются в этих тестах, но async_session контракт требует.
    async def execute(self, stmt: Any) -> Any:
        raise NotImplementedError

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.mark.asyncio
async def test_add_to_prize_pool_acquires_row_lock() -> None:
    """Контракт: SELECT ... FOR UPDATE через session.get(..., with_for_update=True)."""
    habit = make_habit()
    habit.prize_pool = 0
    session = _LockTrackingSession({str(habit.id): habit})
    repo = HabitRepository(session)  # type: ignore[arg-type]

    await repo.add_to_prize_pool(str(habit.id), 100)

    assert len(session.lock_calls) == 1, "должен быть ровно один select"
    entity, ident, opts = session.lock_calls[0]
    assert entity.__name__ == "Habit"
    assert ident == str(habit.id)
    assert opts["with_for_update"] is True, "обязательно with_for_update=True"
    assert habit.prize_pool == 100


@pytest.mark.asyncio
async def test_add_to_prize_pool_accumulates_on_repeat() -> None:
    """Повторные инкременты не теряются (race-фикс: всегда берём свежий row)."""
    habit = make_habit()
    habit.prize_pool = 250
    session = _LockTrackingSession({str(habit.id): habit})
    repo = HabitRepository(session)  # type: ignore[arg-type]

    await repo.add_to_prize_pool(str(habit.id), 50)
    await repo.add_to_prize_pool(str(habit.id), 75)

    assert habit.prize_pool == 250 + 50 + 75
    assert len(session.lock_calls) == 2


@pytest.mark.asyncio
async def test_add_to_prize_pool_missing_habit_is_noop() -> None:
    """Если клуб удалён/архивирован между запросами — не падать, просто вернуться."""
    session = _LockTrackingSession({})
    repo = HabitRepository(session)  # type: ignore[arg-type]

    # Не должно бросить — штраф уже записан, просто prize_pool ушёл в /dev/null.
    await repo.add_to_prize_pool(str(uuid4()), 100)

    assert len(session.lock_calls) == 1
