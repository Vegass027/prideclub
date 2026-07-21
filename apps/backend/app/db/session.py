from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine_kwargs(database_url: str) -> dict:
    """Для in-memory SQLite-тестов невалидны pool_* — отключаем их."""
    if database_url.startswith("sqlite"):
        return {"future": True}
    return {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_timeout": 10,
        "future": True,
    }


def _get_engine():
    """Ленивое создание engine — нужно, чтобы импорт app.models не падал
    на SQLite-тестах, где pool_size/max_overflow недопустимы."""
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url, **_build_engine_kwargs(settings.database_url)
        )
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        _get_engine()
    assert _session_factory is not None
    return _session_factory


# Backward-compat: имя `engine` и `async_session_factory` остались,
# но теперь это property-обёртки над ленивой инициализацией.
class _EngineProxy:
    def __getattr__(self, name: str):
        return getattr(_get_engine(), name)


class _FactoryProxy:
    def __call__(self, *args, **kwargs):
        return _get_session_factory()(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(_get_session_factory(), name)


engine = _EngineProxy()
async_session_factory = _FactoryProxy()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _get_session_factory()() as session:
        yield session