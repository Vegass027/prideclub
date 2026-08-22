"""Admin /admin/v1/stat-definitions — owner-only listing of stat catalog (Phase 3 v2 Task 3.7).

Read-only. 8 canonical rows seeded by migration 019. Used by AdminHabitForm
(Task 3.8) for the "stat choice" dropdown.

Auth: AuthMiddleware /admin/v1/* → owner-only (X-Telegram-Init-Data +
tg_user.id == settings.owner_telegram_id). Никакой дополнительной auth
внутри route.

StatDefinitionRepository: read-only, конструируется inline через
DI provider (per FastAPI Depends pattern). Каждое обращение к repo
создаёт НОВЫЙ instance с текущей AsyncSession — это нормально, т.к.
StatDefinitionRepository stateless (нет кешей/состояния).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.users import TelegramUserDep
from app.core.deps import SessionDep
from app.repositories.stat_definition_repository import (
    StatDefinitionRepository,
)
from app.schemas import (
    AdminStatDefinitionOut,
    AdminStatDefinitionsListResponse,
)

router = APIRouter()


def get_stat_definition_repo(
    session: SessionDep,
) -> StatDefinitionRepository:
    """DI provider для StatDefinitionRepository (read-only, stateless)."""
    return StatDefinitionRepository(session)


@router.get(
    "/stat-definitions",
    response_model=AdminStatDefinitionsListResponse,
)
async def list_stat_definitions(
    _user: TelegramUserDep,  # noqa: ARG001 — auth required (owner)
    repo: Annotated[
        StatDefinitionRepository, Depends(get_stat_definition_repo)
    ],
) -> AdminStatDefinitionsListResponse:
    """Список активных stat_definitions в sort_order ASC (для admin dropdown).

    Auth: AuthMiddleware /admin/v1/* → owner-only.
    Repo: list_active() уже фильтрует is_active=true + сортирует sort_order.
    """
    items = await repo.list_active()
    return AdminStatDefinitionsListResponse(
        items=[
            AdminStatDefinitionOut(
                id=str(sd.id),
                slug=sd.slug,
                name=sd.name,
                icon=sd.icon,
                sort_order=sd.sort_order,
            )
            for sd in items
        ],
        total=len(items),
    )
