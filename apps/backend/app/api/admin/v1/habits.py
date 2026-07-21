"""Admin /admin/v1/habits — owner-only управление клубами (TZ §3.6).

Эндпоинты:
- POST   /admin/v1/habits                 — создать клуб (is_active=false)
- GET    /admin/v1/habits                 — список всех клубов (включая архив)
- GET    /admin/v1/habits/{id}            — детали клуба
- PATCH  /admin/v1/habits/{id}            — частичное обновление
- POST   /admin/v1/habits/{id}/activate   — тумблер is_active
- POST   /admin/v1/habits/{id}/archive    — soft-delete
- POST   /admin/v1/habits/{id}/restore    — снять архив (is_active остаётся false)

Owner-gate происходит на уровне AuthMiddleware (request.state.telegram_user).
Здесь только маршрутизация + DI сервиса.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user
from app.core.constants import ProofType
from app.core.security import TelegramUser
from app.db.session import get_session
from app.models.habit import Habit
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.schemas import (
    AdminHabitActionResponse,
    AdminHabitCreateRequest,
    AdminHabitOut,
    AdminHabitsListResponse,
    AdminHabitToggleRequest,
    AdminHabitUpdateRequest,
)
from app.services.habit_service import HabitService


router = APIRouter()


def _get_habit_service(
    session: AsyncSession = Depends(get_session),
) -> HabitService:
    return HabitService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
    )


def _habit_to_out(habit: Habit, active_members_count: int = 0) -> AdminHabitOut:
    return AdminHabitOut(
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
        is_active=habit.is_active,
        photo_url=habit.photo_url,
        telegram_invite_link=habit.telegram_invite_link,
        stat_name=habit.stat_name,
        stat_icon=habit.stat_icon,
        stat_gain_per_checkin=habit.stat_gain_per_checkin,
        stat_loss_per_miss=habit.stat_loss_per_miss,
        member_limit=habit.member_limit,
        curator_id=habit.curator_id,
        archived_at=habit.archived_at,
        created_at=habit.created_at,
        active_members_count=active_members_count,
    )


@router.post(
    "/habits",
    response_model=AdminHabitOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_habit(
    payload: AdminHabitCreateRequest,
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitOut:
    """Создать клуб. Всегда is_active=false (TZ §3.6.4)."""
    habit = await service.create(
        admin_id=user.id,
        title=payload.title,
        description=payload.description,
        photo_url=payload.photo_url,
        telegram_invite_link=payload.telegram_invite_link,
        stat_name=payload.stat_name,
        stat_icon=payload.stat_icon,
        chat_id=payload.chat_id,
        checkin_window_start=payload.checkin_window_start,
        checkin_window_end=payload.checkin_window_end,
        timezone_str=payload.timezone,
        proof_type=ProofType(payload.proof_type),
        price_month=payload.price_month,
        penalty_amount=payload.penalty_amount,
        stat_gain_per_checkin=payload.stat_gain_per_checkin,
        stat_loss_per_miss=payload.stat_loss_per_miss,
        member_limit=payload.member_limit,
        curator_id=payload.curator_id,
    )
    await service._session.commit()  # noqa: SLF001 — admin endpoint, commit разрешён
    return _habit_to_out(habit)


@router.get("/habits", response_model=AdminHabitsListResponse)
async def list_habits(
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitsListResponse:
    """Все клубы (включая архивированные)."""
    repo = service._habit_repo  # noqa: SLF001
    items = await repo.list_including_archived()
    out: list[AdminHabitOut] = []
    for h in items:
        active_count = await repo.count_active_members(str(h.id))
        out.append(_habit_to_out(h, active_members_count=active_count))
    return AdminHabitsListResponse(items=out)


@router.get("/habits/{habit_id}", response_model=AdminHabitOut)
async def get_habit(
    habit_id: str,
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitOut:
    """Детали клуба (включая архив)."""
    habit = await service._habit_repo.get(habit_id)  # noqa: SLF001
    if habit is None:
        from app.core.exceptions import HabitNotFoundError

        raise HabitNotFoundError()
    active_count = await service._habit_repo.count_active_members(habit_id)  # noqa: SLF001
    return _habit_to_out(habit, active_members_count=active_count)


@router.patch("/habits/{habit_id}", response_model=AdminHabitOut)
async def update_habit(
    habit_id: str,
    payload: AdminHabitUpdateRequest,
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitOut:
    """Частичное обновление полей клуба (TZ §3.6.7 — финансовые заморожены)."""
    fields = payload.model_dump(exclude_unset=True)
    habit = await service.update(
        admin_id=user.id,
        habit_id=habit_id,
        fields=fields,
    )
    await service._session.commit()  # noqa: SLF001
    active_count = await service._habit_repo.count_active_members(habit_id)  # noqa: SLF001
    return _habit_to_out(habit, active_members_count=active_count)


@router.post("/habits/{habit_id}/activate", response_model=AdminHabitActionResponse)
async def activate_habit(
    habit_id: str,
    payload: AdminHabitToggleRequest,
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitActionResponse:
    habit = await service.set_active(
        admin_id=user.id,
        habit_id=habit_id,
        is_active=payload.is_active,
    )
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )


@router.post("/habits/{habit_id}/archive", response_model=AdminHabitActionResponse)
async def archive_habit(
    habit_id: str,
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitActionResponse:
    habit = await service.archive(admin_id=user.id, habit_id=habit_id)
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )


@router.post("/habits/{habit_id}/restore", response_model=AdminHabitActionResponse)
async def restore_habit(
    habit_id: str,
    user: TelegramUser = Depends(current_user),
    service: HabitService = Depends(_get_habit_service),
) -> AdminHabitActionResponse:
    habit = await service.restore(admin_id=user.id, habit_id=habit_id)
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )
