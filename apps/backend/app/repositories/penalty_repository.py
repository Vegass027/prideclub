from __future__ import annotations

from sqlalchemy import func, select
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

    async def totals_for_memberships(
        self,
        membership_ids: list[str],
        *,
        as_violator: bool = True,
    ) -> dict[str, tuple[int, int]]:
        """Возвращает {membership_id: (count, total_kopecks)} для списка memberships.

        as_violator=True — это Penalty.membership_id (юзер пойман).
        as_violator=False — это Penalty.catcher_membership_id (юзер поймал).

        Один SQL: COUNT + COALESCE(SUM, 0). Используется leaderboard для
        breakdown и checkin_service для карточки клуба.
        """
        if not membership_ids:
            return {}
        column = Penalty.membership_id if as_violator else Penalty.catcher_membership_id
        rows = (
            await self._session.execute(
                select(
                    column,
                    func.count(Penalty.id),
                    func.coalesce(func.sum(Penalty.amount), 0),
                )
                .where(column.in_(membership_ids))
                .group_by(column)
            )
        ).all()
        return {str(m_id): (int(c), int(t)) for m_id, c, t in rows}

    async def totals_for_membership(
        self,
        membership_id: str,
        *,
        as_violator: bool = True,
    ) -> tuple[int, int]:
        """Возвращает (count, total_kopecks) для одного membership."""
        result = await self.totals_for_memberships(
            [membership_id], as_violator=as_violator
        )
        return result.get(str(membership_id), (0, 0))
