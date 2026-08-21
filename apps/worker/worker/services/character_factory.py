"""Worker-side composition helper для CharacterService (Phase 3 Task 3.4).

Per Dmitry 21.08.2026: composition concern — НЕ в service layer, а в
composition root. Worker и HTTP не имеют общего DI-пакета →
локальный helper здесь, идентичный по смыслу `get_character_service`
из `app/core/deps.py`.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.stat_definition_repository import (
    StatDefinitionRepository,
)
from app.repositories.user_status_repository import UserStatusRepository
from app.repositories.user_stats_repository import UserStatsRepository
from app.services.character_service import CharacterService


def _build_character_service(session: AsyncSession) -> CharacterService:
    """Compose CharacterService с 3 repos поверх переданной session.

    Не открывает новую. Используется в обоих местах
    CheckinService-инстанций в worker/tasks/process_checkin.py
    и в PenaltyService в process_penalty.py. Один session → одна
    транзакция → check-in/penalty INSERT + user_stats UPDATE
    коммитятся/откатываются атомарно.
    """
    return CharacterService(
        user_stats_repo=UserStatsRepository(session),
        user_status_repo=UserStatusRepository(session),
        stat_definition_repo=StatDefinitionRepository(session),
    )
