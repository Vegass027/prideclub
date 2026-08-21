from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stat_definition import StatDefinition


class StatDefinitionRepository:
    """Read-only каталог канонических характеристик (Phase 3 v2).

    Записи seeded в миграции 019 (8 канонических). Мутаций в MVP нет —
    если когда-то понадобится admin-CRUD для каталога, это отдельная
    задача Phase 5/6 (не Phase 3).

    Per layering rule (AGENTS.md): репозиторий без бизнес-логики,
    никакого commit() — коммит на уровне handler/service.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, id: str) -> StatDefinition | None:
        """SELECT по UUID PK. None если не найдено."""
        result = await self._session.execute(
            select(StatDefinition).where(StatDefinition.id == id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> StatDefinition | None:
        """SELECT по UNIQUE slug. Используется admin / миграциями данных."""
        result = await self._session.execute(
            select(StatDefinition).where(StatDefinition.slug == slug)
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[StatDefinition]:
        """ORDER BY sort_order ASC — для admin dropdown (Task 3.8).

        Caller фильтрует is_active=false через WHERE здесь, а не после
        SELECT — экономия на трафике для админ-страницы.
        """
        result = await self._session.execute(
            select(StatDefinition)
            .where(StatDefinition.is_active.is_(True))
            .order_by(StatDefinition.sort_order)
        )
        return list(result.scalars().all())
