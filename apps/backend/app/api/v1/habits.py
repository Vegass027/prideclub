from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.users import TelegramUserDbDep
from app.core.config import get_settings
from app.core.deps import SessionDep
from app.db.redis import get_redis
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.penalty_repository import PenaltyRepository
from app.schemas import (
    CheckinStatusOut,
    HabitOut,
    MarketplaceResponse,
    MembershipOut,
    MyHabitOut,
    MyHabitsListResponse,
    TodayResponse,
)
from app.services.checkin_service import CheckinService
from app.services.today_cache import RedisTodayCache

router = APIRouter()


@lru_cache(maxsize=1)
def _redis_enabled() -> bool:
    try:
        return bool(get_settings().redis_url)
    except Exception:
        return False


async def get_checkin_service(
    session: SessionDep,
) -> CheckinService:
    cache = RedisTodayCache(get_redis()) if _redis_enabled() else None
    return CheckinService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
        checkin_repo=CheckinRepository(session),
        penalty_repo=PenaltyRepository(session),
        cache=cache,
    )


CheckinServiceDep = Annotated[CheckinService, Depends(get_checkin_service)]


@router.get("/marketplace", response_model=MarketplaceResponse)
async def marketplace(
    session: SessionDep,
    _: TelegramUserDbDep,
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
            proof_types=list(h.proof_types),
            prize_pool=h.prize_pool,
            members_count=c,
            is_active=h.is_active,
            photo_url=h.photo_url,
            telegram_invite_link=h.telegram_invite_link,
            checkin_topic_thread_id=h.checkin_topic_thread_id,
            chat_topic_thread_id=h.chat_topic_thread_id,
        )
        for h, c in rows
    ]
    return MarketplaceResponse(items=items)


@router.get("/habits/{habit_id}/today", response_model=TodayResponse)
async def today(
    habit_id: str,
    service: CheckinServiceDep,
    user: TelegramUserDbDep,
) -> TodayResponse:
    habit, m, stats = await service.get_today_status(
        user_id=user.id, habit_id=habit_id, now_utc=datetime.now(tz=UTC)
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
            proof_types=list(habit.proof_types),
            prize_pool=habit.prize_pool,
            members_count=0,
            is_active=habit.is_active,
            photo_url=habit.photo_url,
            telegram_invite_link=habit.telegram_invite_link,
            checkin_topic_thread_id=habit.checkin_topic_thread_id,
            chat_topic_thread_id=habit.chat_topic_thread_id,
        ),
        membership=MembershipOut.model_validate(m),
        checkin=CheckinStatusOut(
            status=stats.status,
            checkin_count=stats.checkin_count,
            streak_days=stats.streak_days,
            penalties_count=stats.penalties_count,
            penalties_total=stats.penalties_total,
            # Pravki-paused-window-open-2026-08-14: прокидываем сумму штрафа
            # за сегодня для условного рендера "штраф списан" в TodayPage.
            penalty_for_today_kopecks=stats.penalty_for_today_kopecks,
            deadline_at=None,
        ),
    )


@router.get("/me/habits", response_model=MyHabitsListResponse)
async def my_habits(
    session: SessionDep,
    user: TelegramUserDbDep,
) -> MyHabitsListResponse:
    """Список клубов, в которых состоит пользователь.

    Используется в:
    - Global habit picker (`OnboardingPage` / redirect logic): если клубов >1
      — редиректим на /my-habits, если 1 — Today откроется напрямую.
    - ProfilePage "Мои клубы" — бейдж «Членство до {date}» и кнопка
      «Пополнить» рядом (feature/paused-member-ux).
    - MarketplacePage — `isJoined` flag для «Открыть клуб» vs «Вступить».

    Feature/paused-member-ux: возвращает ВСЕ не-LEFT membership'ы
    (ACTIVE + PAUSED), плюс для каждой — `membership_status` и
    `subscription_until` для badge'а. PAUSED-юзер с пустым депозитом
    теперь видит свой клуб и кнопку «Пополнить» — иначе застревает
    на странице без понимания что делать.
    """
    repo = HabitRepository(session)
    rows = await repo.list_for_user_with_membership(user.id)
    items = [
        MyHabitOut(
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
            proof_types=list(h.proof_types),
            prize_pool=h.prize_pool,
            members_count=0,
            is_active=h.is_active,
            photo_url=h.photo_url,
            telegram_invite_link=h.telegram_invite_link,
            checkin_topic_thread_id=h.checkin_topic_thread_id,
            chat_topic_thread_id=h.chat_topic_thread_id,
            # Membership context (feature/paused-member-ux):
            membership_status=m.status.value,
            subscription_until=m.subscription_until,
        )
        for h, m in rows
    ]
    return MyHabitsListResponse(items=items)