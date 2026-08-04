"""SSE endpoints: token issuance + stream.

Этот файл растёт пошагово по плану sse+redis.md редакция 4.

Шаг 1 (c836542): POST /events/stream/token — выдача JWT-токена.
Шаг 2 (этот коммит): GET /events/stream — heartbeat-only skeleton.
Шаг 4:        XREAD в stream, реальные события.

POST /events/stream/token авторизуется через X-Telegram-Init-Data
(обычный /api/v1/* контур, AuthMiddleware валидирует). При успехе выдаёт
короткоживущий (TTL 60с) JWT-токен для авторизации на GET-эндпоинте.

GET /events/stream авторизуется через токен в query (EventSource не
поддерживает кастомные заголовки в браузере). AuthMiddleware делает
exact-path исключение для /api/v1/events/stream, не префикс — будущие
/api/v1/events/* остаются под initData.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.v1.users import TelegramUserDbDep
from app.core.constants import MembershipStatus
from app.core.deps import SessionDep
from app.core.exceptions import (
    InvalidServiceTokenError,
    ServiceTokenExpiredError,
    SseNotConfiguredError,
    SseStreamForbiddenError,
)
from app.repositories.membership_repository import MembershipRepository
from app.services.sse.sse_token import (
    SSE_TOKEN_DEFAULT_TTL_SECONDS,
    generate_sse_token,
    validate_sse_token,
)

router = APIRouter()


# === POST /events/stream/token ==============================================


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


# === GET /events/stream =====================================================

# Heartbeat interval (seconds): держит SSE-соединение живым через nginx
# (proxy_read_timeout) и пробуждает прокси от буферизации. Подтверждено
# пользователем в редакции 3 (sse+redis.md §3.9).
SSE_HEARTBEAT_INTERVAL_SECONDS = 30


async def _sse_heartbeat_generator(request: Request, user_id: int, habit_id: str):
    """Heartbeat-only SSE generator.

    Шаг 2: пустой стрим, только heartbeat. Шаг 4 заменит `await asyncio.sleep`
    на `XREAD BLOCK` (Redis Streams, см. sse+redis.md §2.4).

    При disconnect клиента Starlette отменяет генератор → asyncio.CancelledError.
    Ловим, делаем cleanup (для Step 4 — cancel XREAD task + close Redis client),
    для Step 2 — ничего делать не надо.
    """
    try:
        # Initial comment: flush headers сразу, чтобы клиент увидел 200 OK
        # и не висел в ожидании первого байта.
        yield ": connected\n\n"
        while True:
            await asyncio.sleep(SSE_HEARTBEAT_INTERVAL_SECONDS)
            yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        # Клиент отвалился — чистый выход. Генератор завершается,
        # StreamingResponse закрывает соединение.
        return


@router.get("/events/stream")
async def stream_sse_events(
    request: Request,
    habit_id: str = Query(..., min_length=1),
    token: str = Query(..., min_length=1),
) -> StreamingResponse:
    """SSE-стрим событий по клубу.

    Авторизация: через JWT-токен в query (см. sse_token.py). AuthMiddleware
    НЕ валидирует initData для этого пути (exact-path исключение в
    SSE_AUTH_BYPASS_PATHS), иначе EventSource не сможет передать токен.

    На этом этапе (Step 2) — heartbeat-only skeleton. Реальный XREAD
    появится в Step 4.

    Возвращает text/event-stream с периодическими `: heartbeat\n\n`
    комментариями (SSE-комментарии не считаются событиями у клиента,
    нужны только для keep-alive и пробуждения proxy).
    """
    secret = _settings_secret()
    if not secret:
        raise SseNotConfiguredError()

    # Валидация токена. Доменные исключения InvalidServiceTokenError и
    # ServiceTokenExpiredError пробрасываются — глобальный exception
    # handler вернёт 401 + соответствующий code.
    try:
        payload = validate_sse_token(
            token, secret=secret, expected_habit_id=habit_id
        )
    except (InvalidServiceTokenError, ServiceTokenExpiredError):
        raise

    user_id = int(payload["sub"])

    return StreamingResponse(
        _sse_heartbeat_generator(request, user_id, habit_id),
        media_type="text/event-stream",
        headers={
            # nginx-specific hint: "не буферизируй". Дополняет
            # proxy_buffering off в nginx-конфиге.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
