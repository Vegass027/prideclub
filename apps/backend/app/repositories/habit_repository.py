from __future__ import annotations

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

    async def list_active(self) -> list[Habit]:
        result = await self._session.execute(
            select(Habit).where(Habit.is_active.is_(True)).order_by(Habit.created_at)
        )
        return list(result.scalars().all())

    async def list_with_member_counts(self) -> list[tuple[Habit, int]]:
        stmt = (
            select(Habit, func.count(Membership.id))
            .select_from(Habit)
            .outerjoin(Membership, Membership.habit_id == Habit.id)
            .where(Habit.is_active.is_(True))
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
        habit = await self.get(habit_id)
        if habit is None:
            return
        habit.prize_pool += amount