from __future__ import annotations

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auxiliary import SuspiciousPair


class SuspiciousPairsRepository:
    """Доступ к таблице антифрод-флагов пар участников.

    Канонический ключ пары — упорядоченная пара (min(a,b), max(a,b)),
    чтобы (A,B) и (B,A) давали одну и ту же строку.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _canonical(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    async def lookup_flagged(self, a: str, b: str) -> bool:
        """Возвращает True, если пара (a, b) сейчас в статусе 'flagged'.

        Канонический ключ (min, max) берётся из `_canonical`, поэтому
        пары (A,B) и (B,A) дают один и тот же ответ. Пары в статусе
        'banned' сюда **не** входят — это отдельное состояние. Пары,
        у которых нет строки, → False.

        Используется в `penalty_service` (T2 рефакторинг: раньше
        сервис делал SQL сам).
        """
        if a == b:
            return False
        pair = await self.get(a, b)
        return pair is not None and pair.status == "flagged"

    async def get(self, a: str, b: str) -> SuspiciousPair | None:
        ca, cb = self._canonical(a, b)
        result = await self._session.execute(
            select(SuspiciousPair).where(
                SuspiciousPair.membership_id_a == ca,
                SuspiciousPair.membership_id_b == cb,
            )
        )
        return result.scalar_one_or_none()

    async def flag(
        self,
        *,
        a: str,
        b: str,
        reason: str,
        status: str = "flagged",
    ) -> SuspiciousPair:
        """INSERT ON CONFLICT DO UPDATE — идемпотентный flag.

        Диалект-нейтральный upsert: на Postgres и SQLite (≥ 3.24) работает
        одинаково через `Insert.on_conflict_do_update`. Если пара уже была
        в статусе 'banned', не откатываем бан обратно в 'flagged'.
        """
        ca, cb = self._canonical(a, b)
        bind = self._session.get_bind()
        if bind is None or getattr(bind.dialect, "name", "") == "postgresql":
            insert_stmt = pg_insert(SuspiciousPair)
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            insert_stmt = sqlite_insert(SuspiciousPair)

        stmt = (
            insert_stmt.values(
                membership_id_a=ca,
                membership_id_b=cb,
                reason=reason,
                status=status,
            )
            .on_conflict_do_update(
                index_elements=[
                    SuspiciousPair.membership_id_a,
                    SuspiciousPair.membership_id_b,
                ],
                set_={
                    "reason": reason,
                    "status": case(
                        (SuspiciousPair.status == "banned", "banned"),
                        else_=status,
                    ),
                },
            )
            .returning(SuspiciousPair)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def clear(self, a: str, b: str) -> bool:
        ca, cb = self._canonical(a, b)
        result = await self._session.execute(
            delete(SuspiciousPair).where(
                SuspiciousPair.membership_id_a == ca,
                SuspiciousPair.membership_id_b == cb,
            )
        )
        return (result.rowcount or 0) > 0

    async def ban(self, *, a: str, b: str, reason: str) -> SuspiciousPair:
        return await self.flag(a=a, b=b, reason=reason, status="banned")

    async def list_flagged(
        self,
        *,
        status: str | None = "flagged",
        limit: int = 100,
        offset: int = 0,
    ) -> list[SuspiciousPair]:
        stmt = select(SuspiciousPair).order_by(SuspiciousPair.detected_at.desc())
        if status is not None:
            stmt = stmt.where(SuspiciousPair.status == status)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
