from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user, current_user_db
from app.core.config import get_settings
from app.core.security import TelegramUser
from app.db.redis import get_redis
from app.db.session import get_session
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.schemas import (
    CheckinStatusOut,
    HabitOut,
    MarketplaceResponse,
    MembershipOut,
    TodayResponse,
)
from app.services.checkin_service import CheckinService
from app.services.membership_service import MembershipService
from app.services.today_cache import RedisTodayCache


router = APIRouter()


@lru_cache(maxsize=1)
def _redis_enabled() -> bool:
    try:
        return bool(get_settings().redis_url)
    except Exception:
        return False


async def get_checkin_service(
    session: AsyncSession = Depends(get_session),
) -> CheckinService:
    cache = RedisTodayCache(get_redis()) if _redis_enabled() else None
    return CheckinService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
        checkin_repo=CheckinRepository(session),
        cache=cache,
    )


async def get_membership_service(
    session: AsyncSession = Depends(get_session),
) -> MembershipService:
    return MembershipService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
    )


@router.get("/marketplace", response_model=MarketplaceResponse)
async def marketplace(
    session: AsyncSession = Depends(get_session),
    _: TelegramUser = Depends(current_user_db),
) -> MarketplaceResponse:
    repo = HabitRepository(session)
    rows = await repo.list_with_member_counts()
    items = [
        HabitOut(
            id=str(h.id),
            title=h.title,
            description=h.description,
            chat_id=h.chat_id,
            checkin_window_start=h.checkin_window_start.isoformat(),
            checkin_window_end=h.checkin_window_end.isoformat(),
            timezone=h.timezone,
            penalty_amount=h.penalty_amount,
            price_month=h.price_month,
            proof_type=h.proof_type.value,
            prize_pool=h.prize_pool,
            members_count=c,
            is_active=h.is_active,
        )
        for h, c in rows
    ]
    return MarketplaceResponse(items=items)


@router.get("/habits/{habit_id}/today", response_model=TodayResponse)
async def today(
    habit_id: str,
    service: CheckinService = Depends(get_checkin_service),
    user: TelegramUser = Depends(current_user_db),
) -> TodayResponse:
    habit, m, status, streak = await service.get_today_status(
        user_id=user.id, habit_id=habit_id, now_utc=datetime.now(tz=timezone.utc)
    )
    return TodayResponse(
        habit=HabitOut(
            id=str(habit.id),
            title=habit.title,
            description=habit.description,
            chat_id=habit.chat_id,
            checkin_window_start=habit.checkin_window_start.isoformat(),
            checkin_window_end=habit.checkin_window_end.isoformat(),
            timezone=habit.timezone,
            penalty_amount=habit.penalty_amount,
            price_month=habit.price_month,
            proof_type=habit.proof_type.value,
            prize_pool=habit.prize_pool,
            members_count=0,
            is_active=habit.is_active,
        ),
        membership=MembershipOut.model_validate(m),
        checkin=CheckinStatusOut(
            status=status,
            streak_days=streak,
            deadline_at=None,
        ),
    )


@router.get("/me/habits", response_model=MarketplaceResponse)
async def my_habits(
    session: AsyncSession = Depends(get_session),
    user: TelegramUser = Depends(current_user_db),
) -> MarketplaceResponse:
    """Список клубов, в которых состоит пользователь.

    Используется в глобальном habit picker'е: если клубов >1 — редиректим
    на /my-habits, если 1 — Today откроется напрямую.
    """
    repo = HabitRepository(session)
    rows = await repo.list_for_user(user.id)
    items = [
        HabitOut(
            id=str(h.id),
            title=h.title,
            description=h.description,
            chat_id=h.chat_id,
            checkin_window_start=h.checkin_window_start.isoformat(),
            checkin_window_end=h.checkin_window_end.isoformat(),
            timezone=h.timezone,
            penalty_amount=h.penalty_amount,
            price_month=h.price_month,
            proof_type=h.proof_type.value,
            prize_pool=h.prize_pool,
            members_count=0,
            is_active=h.is_active,
        )
        for h in rows
    ]
    return MarketplaceResponse(items=items)