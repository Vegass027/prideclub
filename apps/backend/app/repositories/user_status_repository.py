from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_status import UserStatus


class UserStatusRepository:
    """Read-only справочник статусов (Phase 3 v2, 5 emoji строк).

    Используется CharacterService.calculate_status (Task 3.3):
    current = последняя строка с min_threshold <= total_value,
    next    = первая строка с min_threshold > total_value
    (или NULL если текущая — максимальная).

    На реальном проде 5 строк, ничего не меняется в runtime —
    можно было бы in-memory кешировать, но в MVP простой SELECT.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all_ordered(self) -> list[UserStatus]:
        """ORDER BY sort_order ASC (sort_order 1→5)."""
        result = await self._session.execute(
            select(UserStatus).order_by(UserStatus.sort_order)
        )
        return list(result.scalars().all())
