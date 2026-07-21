from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user
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


@router.post("/habits/{habit_id}/join")
async def join(
    habit_id: str,
    user: TelegramUser = Depends(current_user),
    service: MembershipService = Depends(get_membership_service),
) -> MembershipOut:
    m = await service.join(user_id=user.id, habit_id=habit_id)
    return MembershipOut.model_validate(m)


@router.post("/habits/{habit_id}/leave")
async def leave(
    habit_id: str,
    user: TelegramUser = Depends(current_user),
    service: MembershipService = Depends(get_membership_service),
) -> MembershipOut:
    m = await service.leave(user_id=user.id, habit_id=habit_id)
    return MembershipOut.model_validate(m)