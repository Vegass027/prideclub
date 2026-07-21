from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class RepoContext:
    session: AsyncSession


class BaseRepository:
    """Все репозитории получают AsyncSession через DI (из роута/celery-task)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session


@asynccontextmanager
async def session_scope(
    factory: AsyncIterator[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Удобный contextmanager для воркеров / Celery."""
    async for session in factory:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
