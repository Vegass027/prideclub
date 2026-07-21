from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MembershipStatus
from app.models.membership import Membership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, membership_id: str) -> Membership | None:
        result = await self._session.execute(
            select(Membership).where(Membership.id == membership_id)
        )
        return result.scalar_one_or_none()

    async def get_for_user_in_habit(
        self, user_id: int, habit_id: str
    ) -> Membership | None:
        result = await self._session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.habit_id == habit_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_habit(self, habit_id: str) -> list[Membership]:
        result = await self._session.execute(
            select(Membership).where(Membership.habit_id == habit_id)
        )
        return list(result.scalars().all())

    async def lock_for_update(self, membership_id: str) -> Membership:
        """SELECT ... FOR UPDATE — для списания депозита."""
        result = await self._session.execute(
            select(Membership).where(Membership.id == membership_id).with_for_update()
        )
        m = result.scalar_one()
        return m

    async def create(self, user_id: int, habit_id: str) -> Membership:
        m = Membership(
            user_id=user_id,
            habit_id=habit_id,
            status=MembershipStatus.ACTIVE,
        )
        self._session.add(m)
        await self._session.flush()
        return m

    async def pause(self, membership_id: str) -> None:
        m = await self.get(membership_id)
        if m is None:
            return
        m.status = MembershipStatus.PAUSED

    async def add_balance(self, membership_id: str, amount: int) -> None:
        m = await self.lock_for_update(membership_id)
        m.deposit_balance += amount