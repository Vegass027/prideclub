"""Reusable Annotated dependency type aliases for FastAPI handlers.

Per FastAPI docs (https://fastapi.tiangolo.com/tutorial/sql-databases/), using
`Annotated[X, Depends(get_x)]` aliases is preferred over the `x: X = Depends(get_x)`
default-argument pattern because:

- it avoids function-call-in-default-argument lint warnings (B008),
- it makes the dependency explicit at the type level,
- improves IDE auto-import / refactor support,
- lets multiple handlers share a single dependency declaration.

Database-level aliases (`SessionDep`, `RedisDep`) live here. Authentication
aliases (`TelegramUserDep`, `TelegramUserDbDep`, `ServiceCallerDep`) live in
`app.api.v1.users` to avoid circular imports.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.telegram_bot_api import get_session as get_bot_http
from app.db.redis import get_redis
from app.db.session import get_session
from app.services.avatar_service import AvatarService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]


def get_avatar_service(
    redis: RedisDep,
) -> AvatarService:
    """DI provider для AvatarService (Pravki.md §7.1).

    Создаёт новый instance на каждый запрос. `http` передаётся как
    фабрика (callable) — AvatarService создаёт ClientSession внутри
    async-метода, где event loop уже привязан. Если передать
    готовый ClientSession здесь — RuntimeError "no running event
    loop" потому что DI выполняется в threadpool, не в event loop.
    """
    return AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,
        http_factory=get_bot_http,
    )


from app.repositories.stat_definition_repository import StatDefinitionRepository
from app.repositories.user_status_repository import UserStatusRepository
from app.repositories.user_stats_repository import UserStatsRepository
from app.services.character_service import CharacterService


def get_character_service(session: SessionDep) -> CharacterService:
    """DI provider для CharacterService (Phase 3 v2, Task 3.4).

    Composes 3 repositories поверх session из FastAPI DI — НЕ
    открывает свою. Это composition concern (не business logic):
    CharacterService остаётся "clean" (только __init__ с 3 args).

    Используется в get_checkin_service (habits.py) и напрямую в
    catch_violator (members.py) для PenaltyService.

    Pravki.md 2026-07-24: расположен рядом с get_avatar_service —
    та же семантика (provider function, разные repos, один
    AsyncSession на запрос).
    """
    return CharacterService(
        user_stats_repo=UserStatsRepository(session),
        user_status_repo=UserStatusRepository(session),
        stat_definition_repo=StatDefinitionRepository(session),
    )


AvatarServiceDep = Annotated[AvatarService, Depends(get_avatar_service)]


__all__ = ["SessionDep", "RedisDep", "AvatarServiceDep", "get_avatar_service"]