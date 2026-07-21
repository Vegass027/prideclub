from __future__ import annotations

from app.core.constants import MembershipStatus
from app.core.exceptions import (
    HabitMemberLimitReachedError,
    HabitNotFoundError,
    MembershipNotActiveError,
    MembershipNotFoundError,
)
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
                # Возобновление: пользователь уже был в клубе, лимит НЕ применяется.
                # иначе бывший член не смог бы вернуться, даже если место освободилось.
                existing.status = MembershipStatus.ACTIVE
                return existing
            return existing

        # Новый участник — проверяем member_limit под блокировкой строки клуба.
        # FOR UPDATE на habit гарантирует, что счётчик участников и INSERT membership
        # выполняются атомарно относительно других параллельных join.
        if habit.member_limit is not None:
            habit = await self._habit_repo.lock_for_update(habit_id)
            if habit is None:
                # Клуб удалили между гейтом и lock — трактуем как not_found.
                raise HabitNotFoundError()
            active_members = await self._habit_repo.count_active_members(habit_id)
            if active_members >= habit.member_limit:
                self._logger.info(
                    "habit_join_rejected_member_limit",
                    extra={
                        "user_id": user_id,
                        "habit_id": habit_id,
                        "member_limit": habit.member_limit,
                        "active_members": active_members,
                    },
                )
                raise HabitMemberLimitReachedError()

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

    async def leave(self, *, user_id: int, habit_id: str) -> Membership:
        m = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if m is None:
            raise MembershipNotFoundError()
        if m.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError()
        m.status = MembershipStatus.LEFT
        return m