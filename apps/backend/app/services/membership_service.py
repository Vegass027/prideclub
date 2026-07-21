from __future__ import annotations

from app.core.constants import MembershipStatus
from app.core.exceptions import HabitNotFoundError, MembershipNotActiveError, MembershipNotFoundError
from app.core.logging import get_logger
from app.models.membership import Membership
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository


class MembershipService:
    def __init__(
        self,
        session,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._logger = get_logger("membership_service")

    async def join(self, *, user_id: int, habit_id: str) -> Membership:
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()

        existing = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if existing is not None:
            if existing.status == MembershipStatus.LEFT:
                existing.status = MembershipStatus.ACTIVE
                return existing
            return existing

        m = await self._membership_repo.create(user_id=user_id, habit_id=habit_id)
        self._logger.info(
            "user_joined_habit",
            extra={"user_id": user_id, "habit_id": habit_id},
        )
        return m

    async def leave(self, *, user_id: int, habit_id: str) -> Membership:
        m = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if m is None:
            raise MembershipNotFoundError()
        if m.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError()
        m.status = MembershipStatus.LEFT
        return m