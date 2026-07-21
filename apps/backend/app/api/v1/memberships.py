from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user_db
from app.core.exceptions import HabitArchivedError, HabitInactiveError
from app.core.security import TelegramUser
from app.db.session import get_session
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.schemas import MembershipOut
from app.services.membership_service import MembershipService


router = APIRouter()


async def get_membership_service(
    session: AsyncSession = Depends(get_session),
) -> MembershipService:
    return MembershipService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
    )


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
    user: TelegramUser = Depends(current_user_db),
    service: MembershipService = Depends(get_membership_service),
    session: AsyncSession = Depends(get_session),
) -> MembershipOut:
    habit_repo = HabitRepository(session)
    await _ensure_joinable(habit_repo, habit_id)
    m = await service.join(user_id=user.id, habit_id=habit_id)
    await session.commit()
    return MembershipOut.model_validate(m)


@router.post("/habits/{habit_id}/leave")
async def leave(
    habit_id: str,
    user: TelegramUser = Depends(current_user_db),
    service: MembershipService = Depends(get_membership_service),
    session: AsyncSession = Depends(get_session),
) -> MembershipOut:
    m = await service.leave(user_id=user.id, habit_id=habit_id)
    await session.commit()
    return MembershipOut.model_validate(m)