from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CheckinStatus
from app.models.checkin import Checkin


class CheckinRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_date(
        self, membership_id: str, on_date
    ) -> Checkin | None:
        result = await self._session.execute(
            select(Checkin).where(
                Checkin.membership_id == membership_id,
                Checkin.date == on_date,
            )
        )
        return result.scalar_one_or_none()

    async def count_done_for_memberships(
        self, membership_ids: list[str]
    ) -> dict[str, int]:
        """Возвращает {membership_id: COUNT(done)} для списка memberships.

        Один SQL: GROUP BY membership_id. Используется leaderboard для
        breakdown и members API для checkin_count.
        """
        if not membership_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Checkin.membership_id, func.count(Checkin.id))
                .where(
                    Checkin.membership_id.in_(membership_ids),
                    Checkin.status == CheckinStatus.DONE,
                )
                .group_by(Checkin.membership_id)
            )
        ).all()
        return {str(m_id): int(c) for m_id, c in rows}

    async def count_done_for_membership(self, membership_id: str) -> int:
        result = await self.count_done_for_memberships([membership_id])
        return result.get(str(membership_id), 0)

    async def get_recent_dates(
        self,
        membership_id: str,
        up_to,
        *,
        limit: int = 90,
    ) -> list:
        """Возвращает даты done-чекинов membership_id ≤ up_to, отсортированные DESC.

        Используется `CheckinService._compute_streak` для подсчёта текущей
        серии — потребитель сравнивает даты по убыванию и кладёт +1 за каждый
        смежный день, начиная с up_to и назад.

        `limit` ограничивает размер выборки (по умолчанию 90 ≈ сезон).
        """
        result = await self._session.execute(
            select(Checkin.date)
            .where(
                Checkin.membership_id == membership_id,
                Checkin.date <= up_to,
                Checkin.status == CheckinStatus.DONE,
            )
            .order_by(Checkin.date.desc())
            .limit(limit)
        )
        return [row[0] for row in result.all()]

    async def get_or_create_done(
        self, *, membership_id: str, on_date, proof_message_id: int
    ) -> tuple[Checkin, bool]:
        """INSERT ON CONFLICT DO NOTHING — идемпотентно.

        Returns (checkin, created_now).
        """
        stmt = (
            pg_insert(Checkin)
            .values(
                membership_id=membership_id,
                date=on_date,
                status=CheckinStatus.DONE,
                proof_message_id=proof_message_id,
            )
            .on_conflict_do_nothing(
                index_elements=["membership_id", "date"]
            )
            .returning(Checkin)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            return row, True
        existing = await self.get_for_date(membership_id, on_date)
        assert existing is not None
        return existing, False