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
    TooManySseConnectionsError,
)
from app.core.logging import get_logger
from app.db.redis import get_redis
from app.repositories.membership_repository import MembershipRepository
from app.services.sse.connection_limiter import SseConnectionLimiter
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


async def _sse_heartbeat_generator(
    request: Request,
    user_id: int,
    habit_id: str,
    connection_limiter: SseConnectionLimiter,
    last_event_id: str | None = None,
):
    """Heartbeat-only SSE generator.

    Шаг 2: пустой стрим, только heartbeat. Шаг 4 заменит `await asyncio.sleep`
    на `XREAD BLOCK` (Redis Streams, см. sse+redis.md §2.4). `last_event_id`
    принимается уже сейчас (контракт эндпоинта) — в Step 4 используется
    как начальный ID для XREAD вместо `$`.

    Cleanup покрывает ВСЕ пути выхода из генератора:
    - is_disconnected() → True (клиент закрыл EventSource по HTTP) → break → finally → release
    - CancelledError (uvicorn worker shutdown / Starlette при разрыве) → except → finally → release
    - нормальный return (теоретически возможен в Step 4 при закрытии по логике) → finally → release

    finally освобождает слот в connection_limiter, иначе утечка под нагрузкой.
    """
    try:
        # Initial comment: flush headers сразу, чтобы клиент увидел 200 OK
        # и не висел в ожидании первого байта.
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                # Клиент закрыл EventSource по HTTP. Выходим чисто через finally.
                break
            await asyncio.sleep(SSE_HEARTBEAT_INTERVAL_SECONDS)
            yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        # Генератор отменён снаружи (Starlette при разрыве, uvicorn graceful shutdown).
        # finally всё равно выполнится ниже.
        return
    finally:
        # Освобождаем слот в Redis. Идемпотентно через clamp-decr.
        # Если Redis временно недоступен — логируем warning, не валим
        # генератор. TTL=180с в connection_limiter страхует от permanent leak.
        try:
            await connection_limiter.release(user_id)
        except Exception as exc:  # noqa: BLE001 — cleanup не должен ронять генератор
            get_logger("sse_generator").warning(
                "sse_release_failed",
                extra={"user_id": user_id, "habit_id": habit_id, "error": str(exc)},
            )


@router.get("/events/stream")
async def stream_sse_events(
    request: Request,
    habit_id: str = Query(..., min_length=1),
    token: str = Query(..., min_length=1),
    last_event_id: str | None = Query(
        None,
        description=(
            "Опциональный Redis Streams ID для resume позиции. "
            "Нативный EventSource шлёт Last-Event-ID только в рамках одной "
            "инстанции; для ручного reconnect'а с новым токеном фронт "
            "передаёт last_event_id через query (см. sse+redis.md §2.4). "
            "Step 2 принимает параметр, Step 4 использует в XREAD."
        ),
    ),
) -> StreamingResponse:
    """SSE-стрим событий по клубу.

    Авторизация: через JWT-токен в query (см. sse_token.py). AuthMiddleware
    НЕ валидирует initData для этого пути (exact-path исключение в
    SSE_AUTH_BYPASS_PATHS), иначе EventSource не сможет передать токен.

    Параметр `last_event_id` принимается уже сейчас (Step 2), чтобы
    фронт (Step 6) мог слать его с первого дня, и чтобы сигнатура
    эндпоинта не менялась при подключении реального XREAD в Step 4.
    На этом этапе он никак не используется (Step 4 начнёт читать из
    Redis Streams начиная с этого ID).

    Возвращает text/event-stream с периодическими `: heartbeat\\n\\n`
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

    # Per-user concurrency limit — защита от DoS через replayable SSE-токен.
    # TTL токена 60с (не одноразовый), иначе атакующий может открыть
    # неограниченное число соединений с одним валидным токеном.
    # Lua-атомарный check-and-incr с rollback (см. connection_limiter.py).
    connection_limiter = SseConnectionLimiter(get_redis())
    if not await connection_limiter.try_acquire(user_id):
        # Слот не занят — лимит исчерпан. 429, не 503 — это per-user, не server-wide.
        raise TooManySseConnectionsError()

    return StreamingResponse(
        _sse_heartbeat_generator(
            request, user_id, habit_id, connection_limiter, last_event_id
        ),
        media_type="text/event-stream",
        headers={
            # nginx-specific hint: "не буферизируй". Дополняет
            # proxy_buffering off в nginx-конфиге.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
