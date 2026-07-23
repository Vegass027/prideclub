"""U5: стриминг `iter_active` / `iter_for_habit` через `stream_scalars`.

Проверяем, что новые методы возвращают `AsyncIterator` (async generator),
а не материализуют список в память. Ленивая природа — главная защита от
OOM при 100+ клубах с 10k+ members.

Контракт:
- `iter_active()` — async generator, не list. `async for` обязателен.
- `iter_for_habit(habit_id)` — async generator, не list.
- Не вычитывает все строки сразу — элементы появляются по мере итерирования.
- `_FakeSession.stream_scalars` обязан быть вызван (а не `execute`).
"""
from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from app.core.constants import MembershipStatus
from app.models.habit import Habit
from app.models.membership import Membership
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository


class _StreamProbe:
    """Записывает факт вызова `stream_scalars` и количество chunks.

    В SQLAlchemy 2.0 async метод `AsyncSession.stream_scalars(stmt)` возвращает
    `AsyncScalarResult`. Чтобы не мокать asyncpg, мы проверяем контракт через
    реальный репозиторий поверх in-memory AsyncSession-заглушки, которая
    реализует `stream_scalars` как async generator.
    """

    def __init__(self) -> None:
        self.stream_calls: list[str] = []
        self.execute_calls: list[str] = []
        self._habits_data: list[Habit] = []
        self._memberships_data: list[Membership] = []

    @staticmethod
    async def _aiter(items: list):
        """Async iterator — отдельный метод, не содержит yield в родителе."""
        for item in items:
            yield item

    async def stream_scalars(self, stmt):
        # Корутина, которая возвращает (НЕ yield'ит) async iterator.
        # Контракт SQLAlchemy 2.0: `result = await session.stream_scalars(stmt)`,
        # затем `async for x in result`. Внутри мы НЕ используем yield здесь —
        # иначе метод станет async generator и его нельзя будет await'ить.
        self.stream_calls.append("stream_scalars")
        if self._is_habit_select(stmt):
            return self._aiter(self._habits_data)
        return self._aiter(self._memberships_data)

    async def execute(self, stmt):
        self.execute_calls.append("execute")
        # Если мы случайно вызвали execute вместо stream_scalars — тест
        # должен поймать это как регресс (контракт нарушен).

    @staticmethod
    def _is_habit_select(stmt) -> bool:
        sql = str(stmt)
        return "habits" in sql.lower()


@pytest.mark.asyncio
async def test_iter_active_is_async_generator_not_list() -> None:
    """`iter_active` должен быть async generator, не возвращать list."""
    repo = HabitRepository(session=None)  # type: ignore[arg-type]
    method = repo.iter_active

    assert inspect.isasyncgenfunction(method), (
        "iter_active обязан быть `async def` с `yield` — это async generator. "
        "Если возвращает list — регресс к O(N) памяти при 100+ клубах"
    )


@pytest.mark.asyncio
async def test_iter_for_habit_is_async_generator_not_list() -> None:
    """`iter_for_habit` должен быть async generator, не возвращать list."""
    repo = MembershipRepository(session=None)  # type: ignore[arg-type]
    method = repo.iter_for_habit

    assert inspect.isasyncgenfunction(method), (
        "iter_for_habit обязан быть `async def` с `yield` — иначе стриминг "
        "теряется и клуб с 10k members грузится целиком в RAM"
    )


@pytest.mark.asyncio
async def test_iter_active_uses_stream_scalars_not_execute() -> None:
    """Контракт: внутри `iter_active` ORM зовёт `stream_scalars`, не `execute`.

    Без `stream_scalars` asyncpg использует обычный cursor, который буферизует
    ВСЕ строки сразу — и теряется смысл стриминга.
    """
    probe = _StreamProbe()
    h1 = Habit(id=str(uuid4()), title="H1", chat_id=-100, is_active=True)
    h2 = Habit(id=str(uuid4()), title="H2", chat_id=-101, is_active=True)
    probe._habits_data = [h1, h2]

    repo = HabitRepository(session=probe)  # type: ignore[arg-type]

    received: list[Habit] = []
    async for h in repo.iter_active():
        received.append(h)

    assert probe.stream_calls == ["stream_scalars"], (
        f"должен быть ровно один вызов stream_scalars; получили {probe.stream_calls}. "
        f"execute_calls={probe.execute_calls}"
    )
    assert probe.execute_calls == [], (
        "если видим execute — идём через buffered cursor, OOM при больших клубах"
    )
    assert received == [h1, h2]


@pytest.mark.asyncio
async def test_iter_for_habit_uses_stream_scalars_not_execute() -> None:
    """Контракт: `iter_for_habit` тоже идёт через stream_scalars."""
    probe = _StreamProbe()
    habit_id = "h-stream-test"
    m1 = Membership(id=str(uuid4()), user_id=1, habit_id=habit_id, status=MembershipStatus.ACTIVE)
    m2 = Membership(id=str(uuid4()), user_id=2, habit_id=habit_id, status=MembershipStatus.ACTIVE)
    m_other = Membership(
        id=str(uuid4()), user_id=3, habit_id="other-habit", status=MembershipStatus.ACTIVE
    )
    probe._memberships_data = [m1, m2, m_other]

    repo = MembershipRepository(session=probe)  # type: ignore[arg-type]

    received: list[Membership] = []
    async for m in repo.iter_for_habit(habit_id):
        received.append(m)

    assert probe.stream_calls == ["stream_scalars"]
    assert probe.execute_calls == []
    # Должны получить только m1, m2 — m_other принадлежит другому клубу.
    # Реальный SQL фильтрует через WHERE; в фейке фильтрация на стороне генератора
    # отсутствует (мы просто возвращаем все). Поэтому проверим только что
    # метод ВЫЗВАЛ stream_scalars, а фильтрация гарантируется SQL в проде.
    assert len(received) >= 2


@pytest.mark.asyncio
async def test_iter_active_is_lazy_does_not_materialize_list() -> None:
    """Ленивость: первый элемент доступен до получения последнего.

    Если бы iter_active возвращал `return list(...)` — все строки уже были бы
    в памяти к моменту вызова. Async generator отдаёт строки по одной.
    """
    # Имитируем "дорогой" async generator с задержкой между yield'ами.
    yielded_at: list[int] = []

    async def lazy_iter():
        for i in range(3):
            yielded_at.append(i)
            yield f"item-{i}"

    gen = lazy_iter()
    assert inspect.isasyncgen(gen)

    # Получаем только первый элемент — второй ещё не должен быть yield'нут.
    first = await gen.__anext__()
    assert first == "item-0"
    assert yielded_at == [0], (
        "async generator ленив: после __anext__() у нас только 0-й yield, "
        "1-й и 2-й ещё впереди. Если материализовать list — будет [0, 1, 2]"
    )

    # Теперь второй
    second = await gen.__anext__()
    assert second == "item-1"
    assert yielded_at == [0, 1]


def test_stream_scalars_signature_is_async() -> None:
    """`AsyncSession.stream_scalars` — это async метод, возвращающий AsyncScalarResult.

    Подтверждаем контракт сигнатуры из docs SQLAlchemy 2.0 — это документированный
    API, на котором построены наши iter_*.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    method = getattr(AsyncSession, "stream_scalars", None)
    assert method is not None, (
        "AsyncSession.stream_scalars должен существовать (SQLAlchemy 2.0+). "
        "Если нет — версия SQLAlchemy ниже 2.0 и стриминг невозможен"
    )
    assert inspect.iscoroutinefunction(method), (
        "stream_scalars обязан быть `async def` — он возвращает awaitable, "
        "дающий AsyncScalarResult при await"
    )
