"""SSE endpoints: token issuance + (будущий) stream.

Этот файл растёт пошагово по плану sse+redis.md редакция 4.

Шаг 1 (этот коммит): только POST /events/stream/token.
Шаг 2 (следующий коммит): GET /events/stream — heartbeat-only skeleton.
Шаг 4: XREAD в stream.

POST /events/stream/token авторизуется через X-Telegram-Init-Data
(обычный /api/v1/* контур, middleware валидирует). При успехе выдаёт
короткоживущий (TTL 60с) JWT-токен для авторизации на GET-эндпоинте.

GET /events/stream авторизуется через токен в query (EventSource не
поддерживает кастомные заголовки в браузере). Middleware делает exact-path
исключение для /api/v1/events/stream, не префикс — будущие /api/v1/events/*
остаются под initData.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.users import TelegramUserDbDep
from app.core.constants import MembershipStatus
from app.core.deps import SessionDep
from app.core.exceptions import SseNotConfiguredError, SseStreamForbiddenError
from app.repositories.membership_repository import MembershipRepository
from app.services.sse.sse_token import (
    SSE_TOKEN_DEFAULT_TTL_SECONDS,
    generate_sse_token,
)

router = APIRouter()


class SseTokenRequest(BaseModel):
    habit_id: str = Field(..., min_length=1)


class SseTokenResponse(BaseModel):
    token: str
    expires_at: datetime


def _settings_secret() -> str:
    """Ленивое чтение SSE_TOKEN_SECRET, чтобы тесты с monkeypatch работали."""
    from app.core.config import get_settings

    return get_settings().sse_token_secret


@router.post("/events/stream/token", response_model=SseTokenResponse)
async def issue_sse_stream_token(
    body: SseTokenRequest,
    session: SessionDep,
    user: TelegramUserDbDep,
) -> SseTokenResponse:
    """Выдать короткоживущий SSE-токен для клуба `habit_id`.

    403 `membership_not_active` — юзер не состоит в клубе активным
    member'ом (или клуб не существует — оба случая трактуются одинаково
    на этом этапе, ранний fail-fast до открытия EventSource).

    503 `sse_not_configured` — SSE_TOKEN_SECRET не задан в env (ops-мисконфиг,
    не баг юзера).

    Зачем membership-check здесь, а не в GET-эндпоинте:
    - Клиент сразу видит 403, не открывает EventSource зря.
    - Логи чище: "SSE не работает" → не membership, а сеть/proxy/токен.
    - Нет паразитного XREAD на пустом стриме (uvicorn worker занят на
      30с блокирующего чтения ради нуля событий).
    """
    repo = MembershipRepository(session)
    membership = await repo.get_for_user_in_habit(user.id, body.habit_id)
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise SseStreamForbiddenError()

    secret = _settings_secret()
    if not secret:
        raise SseNotConfiguredError()

    token, exp_unix = generate_sse_token(
        user_id=user.id,
        habit_id=body.habit_id,
        secret=secret,
        ttl_seconds=SSE_TOKEN_DEFAULT_TTL_SECONDS,
    )
    return SseTokenResponse(
        token=token,
        expires_at=datetime.fromtimestamp(exp_unix, tz=UTC),
    )
