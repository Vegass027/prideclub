from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.users import TelegramUserDbDep
from app.core.deps import SessionDep
from app.core.exceptions import HabitArchivedError, HabitInactiveError
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.schemas import MembershipOut
from app.services.membership_service import MembershipService

router = APIRouter()


async def get_membership_service(
    session: SessionDep,
) -> MembershipService:
    return MembershipService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
    )


MembershipServiceDep = Annotated[MembershipService, Depends(get_membership_service)]


async def _ensure_joinable(habit_repo: HabitRepository, habit_id: str) -> None:
    """Гейт TZ §3.6.6: join запрещён для архивных и неактивных клубов."""
    habit = await habit_repo.get(habit_id)
    if habit is None or habit.archived_at is not None:
        raise HabitArchivedError()
    if not habit.is_active:
        raise HabitInactiveError()


@router.post("/habits/{habit_id}/join")
async def join(
    habit_id: str,
    user: TelegramUserDbDep,
    service: MembershipServiceDep,
    session: SessionDep,
) -> MembershipOut:
    habit_repo = HabitRepository(session)
    await _ensure_joinable(habit_repo, habit_id)
    m = await service.join(user_id=user.id, habit_id=habit_id)
    await session.commit()
    return MembershipOut.model_validate(m)


@router.post("/habits/{habit_id}/leave")
async def leave(
    habit_id: str,
    user: TelegramUserDbDep,
    service: MembershipServiceDep,
    session: SessionDep,
) -> MembershipOut:
    m = await service.leave(user_id=user.id, habit_id=habit_id)
    await session.commit()
    return MembershipOut.model_validate(m)