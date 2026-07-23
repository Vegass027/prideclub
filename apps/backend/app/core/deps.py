"""Reusable Annotated dependency type aliases for FastAPI handlers.

Per FastAPI docs (https://fastapi.tiangolo.com/tutorial/sql-databases/), using
`Annotated[X, Depends(get_x)]` aliases is preferred over the `x: X = Depends(get_x)`
default-argument pattern because:

- it avoids function-call-in-default-argument lint warnings (B008),
- it makes the dependency explicit at the type level,
- it improves IDE auto-import / refactor support,
- it lets multiple handlers share a single dependency declaration.

Database-level aliases (`SessionDep`, `RedisDep`) live here. Authentication
aliases (`TelegramUserDep`, `TelegramUserDbDep`, `ServiceCallerDep`) live in
`app.api.v1.users` to avoid circular imports.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis import get_redis
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]


__all__ = ["SessionDep", "RedisDep"]