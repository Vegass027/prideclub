from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auxiliary import BonusRule


class BonusRuleRepository:
    """Доступ к таблице bonus_rules.

    BonusService исторически брал правило через коллбэк `rule_lookup(event, threshold)` —
    рефакторинг T3 выносит это в репозиторий.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, event_type: str, *, threshold: int) -> BonusRule | None:
        result = await self._session.execute(
            select(BonusRule).where(
                BonusRule.event_type == event_type,
                BonusRule.threshold == threshold,
            )
        )
        return result.scalar_one_or_none()
