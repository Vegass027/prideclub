from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, *, id: int, first_name: str, username: str | None
    ) -> User:
        """INSERT ON CONFLICT DO UPDATE — идемпотентное обновление."""
        stmt = (
            pg_insert(User)
            .values(id=id, first_name=first_name, username=username)
            .on_conflict_do_update(
                index_elements=[User.id],
                set_={"first_name": first_name, "username": username},
            )
            .returning(User)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get(self, user_id: int) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()