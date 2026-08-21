from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.penalty import Penalty


class PenaltyRepository:
    """Доступ к таблице penalties для read-only потребителей.

    После Phase 8 (cleanup bonus mechanics) PenaltyRepository обслуживает
    только новую механику (Pravki-catcher-deposit, Phase 1 Task 1.3) —
    виртуальная бонусная механика полностью удалена.
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

    async def ids_with_any_penalty_today(
        self,
        *,
        membership_ids: list[str],
        club_date,
    ) -> set[str]:
        """Возвращает {membership_id} для которых есть ЛЮБОЙ Penalty за club_date.

        Pravki-bug-fixes §Z-21 (can_catch fix): даже если Checkin на
        сегодня ещё не записан (apply_window_expired применяется раньше),
        сам факт Penalty за день означает что юзер уже получил штраф
        (cron `close_catch_window` или apply_catch) и повторный catch
        даст amount=0 / penalty_already_processed — поэтому can_catch=False.

        Используется в `members.py` для одного batch-запроса по всем
        членам клуба (по аналогии с `counts`).
        """
        if not membership_ids:
            return set()
        result = await self._session.execute(
            select(Penalty.membership_id).where(
                Penalty.membership_id.in_(membership_ids),
                Penalty.date == club_date,
            )
        )
        return {str(m_id) for m_id, in result.all()}

    async def has_any_penalty_today(
        self,
        *,
        membership_id: str,
        club_date,
    ) -> bool:
        """Single-membership shortcut над `ids_with_any_penalty_today`."""
        return membership_id in await self.ids_with_any_penalty_today(
            membership_ids=[membership_id],
            club_date=club_date,
        )

    async def amount_for_today(
        self,
        *,
        membership_id: str,
        club_date,
    ) -> int:
        """Сумма penalty за club_date для одного membership (в копейках).

        Pravki-paused-window-open-2026-08-14: для TodayResponse — нужно
        отличить "пропуск + штраф списан" от "пропуск без штрафа"
        (когда apply_window_expired вернул None из-за deposit=0).
        Возвращает 0 если штрафа нет. Использует один SQL-запрос с
        COALESCE(SUM, 0).
        """
        result = await self._session.execute(
            select(func.coalesce(func.sum(Penalty.amount), 0)).where(
                Penalty.membership_id == membership_id,
                Penalty.date == club_date,
            )
        )
        return int(result.scalar_one())
