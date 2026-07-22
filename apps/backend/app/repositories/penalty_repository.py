from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.penalty import Penalty


class PenaltyRepository:
    """Доступ к таблице penalties для bonus_service и других read-only потребителей.

    BonusService исторически брал Penalty через коллбэк `penalty_lookup` —
    рефакторинг T3 выносит это в репозиторий (sql только в repositories/, не в services/).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, penalty_id: str) -> Penalty | None:
        result = await self._session.execute(
            select(Penalty).where(Penalty.id == penalty_id)
        )
        return result.scalar_one_or_none()
