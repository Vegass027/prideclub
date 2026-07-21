from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.habit import Habit
from app.models.membership import Membership


class HabitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, habit_id: str) -> Habit | None:
        result = await self._session.execute(select(Habit).where(Habit.id == habit_id))
        return result.scalar_one_or_none()

    async def get_by_chat_id(self, chat_id: int) -> Habit | None:
        result = await self._session.execute(select(Habit).where(Habit.chat_id == chat_id))
        return result.scalar_one_or_none()

    async def lock_for_update(self, habit_id: str) -> Habit | None:
        """SELECT ... FOR UPDATE — для атомарной проверки лимита/счётчика в join."""
        return await self._session.get(Habit, habit_id, with_for_update=True)

    async def list_active(self) -> list[Habit]:
        """Клубы, видимые пользователям (marketplace). TZ §3.6.6."""
        result = await self._session.execute(
            select(Habit)
            .where(Habit.is_active.is_(True), Habit.archived_at.is_(None))
            .order_by(Habit.created_at)
        )
        return list(result.scalars().all())

    async def iter_active(self) -> AsyncIterator[Habit]:
        """Стриминг активных клубов через `stream_scalars` (SQLAlchemy 2.0 async).

        Использовать вместо `list_active()` в Celery-тасках и фоновых задачах,
        где нагрузка потенциально большая (100+ клубов с десятками тысяч
        участников суммарно). Экономит память O(1) на итерацию вместо O(N).

        Контракт:
        - Возвращает `AsyncIterator[Habit]` — `async for habit in repo.iter_active()`.
        - Не материализует весь список в память: ORM использует server-side
          cursor (asyncpg) и тащит строки по мере итерирования.
        - Транзакция остаётся открытой на время итерации (вызывающий код
          делает `session.commit()` после).
        """
        result = await self._session.stream_scalars(
            select(Habit)
            .where(Habit.is_active.is_(True), Habit.archived_at.is_(None))
            .order_by(Habit.created_at)
        )
        async for habit in result:
            yield habit

    async def list_including_archived(self) -> list[Habit]:
        """Все клубы, включая архивированные — для админки."""
        result = await self._session.execute(
            select(Habit).order_by(Habit.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_with_member_counts(self) -> list[tuple[Habit, int]]:
        stmt = (
            select(Habit, func.count(Membership.id))
            .select_from(Habit)
            .outerjoin(Membership, Membership.habit_id == Habit.id)
            .where(Habit.is_active.is_(True), Habit.archived_at.is_(None))
            .group_by(Habit.id)
            .order_by(Habit.created_at)
        )
        return [(h, c) for h, c in (await self._session.execute(stmt)).all()]

    async def list_for_user(self, user_id: int) -> list[Habit]:
        """Клубы, в которых состоит пользователь (active memberships)."""
        stmt = (
            select(Habit)
            .join(Membership, Membership.habit_id == Habit.id)
            .where(
                Membership.user_id == user_id,
                Membership.status == "active",
                Habit.is_active.is_(True),
            )
            .order_by(Habit.created_at)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add_to_prize_pool(self, habit_id: str, amount: int) -> None:
        """Атомарный инкремент prize_pool.

        SELECT ... FOR UPDATE защищает от гонки между одновременными штрафами
        (apply_catch + apply_window_expired), приходящими из разных Celery-тасок.
        Без блокировки оба читают prize_pool=N, оба пишут +=amount, один из
        апдейтов пропадает → деньги теряются.

        Используем `session.get(..., with_for_update=True)` — идиоматичный
        SQLAlchemy 2.0-путь для лока одной строки по PK. Метаданные
        загруженной модели остаются в identity map, поэтому `+=` пишет
        через ORM-механизм flush'а в конце транзакции.
        """
        habit = await self._session.get(Habit, habit_id, with_for_update=True)
        if habit is None:
            return
        habit.prize_pool += amount

    async def count_active_members(self, habit_id: str) -> int:
        """Сколько memberships с status != 'left'. Используется для заморозки финансовых полей."""
        result = await self._session.execute(
            select(func.count(Membership.id)).where(
                Membership.habit_id == habit_id,
                Membership.status != "left",
            )
        )
        return int(result.scalar_one())

    async def create(self, *, fields: dict[str, Any]) -> Habit:
        habit = Habit(**fields)
        self._session.add(habit)
        await self._session.flush()
        return habit

    async def update(self, habit: Habit, *, fields: dict[str, Any]) -> Habit:
        for key, value in fields.items():
            setattr(habit, key, value)
        await self._session.flush()
        return habit

    async def archive(self, habit: Habit, *, archived_at: datetime) -> None:
        habit.is_active = False
        habit.archived_at = archived_at
        await self._session.flush()

    async def restore(self, habit: Habit) -> None:
        habit.archived_at = None
        # is_active НЕ меняем — админ явно активирует через /activate (TZ §3.6.8).
        await self._session.flush()

    async def set_active(self, habit: Habit, *, is_active: bool) -> None:
        habit.is_active = is_active
        await self._session.flush()