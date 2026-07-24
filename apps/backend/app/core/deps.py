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

    Создаёт новый instance на каждый запрос (легковесный — только три
    ссылки). aiohttp.ClientSession — singleton (TCP keep-alive pool).
    """
    return AvatarService(
        bot_token=get_settings().bot_token,
        redis=redis,
        http=get_bot_http(),
    )


AvatarServiceDep = Annotated[AvatarService, Depends(get_avatar_service)]


__all__ = ["SessionDep", "RedisDep", "AvatarServiceDep", "get_avatar_service"]