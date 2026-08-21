"""Phase 3 v2 Task 3.6 — /api/v1/character/me endpoint.

Тонкий API-слой над CharacterService (Task 3.3). Сериализация
datetime делается на Pydantic-схеме, не здесь (per Phase 3.3 invariant).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.deps import get_character_service
from app.api.v1.users import TelegramUserDbDep
from app.schemas import CharacterResponse
from app.services.character_service import CharacterService

router = APIRouter()


@router.get("/character/me", response_model=CharacterResponse)
async def get_my_character(
    user: TelegramUserDbDep,
    character_service: Annotated[
        CharacterService, Depends(get_character_service)
    ],
) -> CharacterResponse:
    """Глобальная карточка персонажа.

    CharacterService.get_character возвращает dict с полями
    total_value / status / stats. Pydantic v2 сам распознаёт вложенные
    статус и список stats → CharacterResponse(status=CharacterStatusInfo(...),
    stats=[CharacterStatOut(...)]).

    last_checkin_at остаётся datetime | None в service-слое; Pydantic
    сериализует в ISO (или null) на API-уровне.
    """
    payload = await character_service.get_character(user_id=user.id)
    return CharacterResponse(**payload)
