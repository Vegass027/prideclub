"""SSE endpoints: token issuance + stream.

Этот файл растёт пошагово по плану sse+redis.md редакция 6.

Шаг 1 (c836542): POST /events/stream/token — выдача JWT-токена.
Шаг 2 (9d5b374): GET /events/stream — heartbeat-only skeleton.
Шаг 4 (288e8ae): GET /events/stream — XREAD-цикл в Redis Stream
                  + heartbeat между пустыми чтениями + Last-Event-ID
                  приоритет (header > query > $).
PostReview fix: async-Redis singleton из db/redis_async.py вместо
                `from_url()` per request (FD-leak при reconnect-loop).

POST /events/stream/token авторизуется через X-Telegram-Init-Data
(обычный /api/v1/* контур, AuthMiddleware валидирует). При успехе выдаёт
короткоживущий (TTL 60с) JWT-токен для авторизации на GET-эндпоинте.

GET /events/stream авторизуется через токен в query (EventSource не
поддерживает кастомные заголовки в браузере). AuthMiddleware делает
exact-path исключение для /api/v1/events/stream, не префикс — будущие
/api/v1/events/* остаются под initData.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Header, Query, Request
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
from app.db.redis_async import get_async_redis
from app.repositories.membership_repository import MembershipRepository
from app.services.sse.connection_limiter import SseConnectionLimiter
from app.services.sse.redis_stream_bus import RedisStreamBus
from app.services.sse.sse_formatter import (
    format_connected_comment,
    format_event_frame,
    format_heartbeat_comment,
)
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

# Heartbeat interval уже не отдельная переменная — теперь это
# `RedisStreamBus.block_ms` (30с по умолчанию, см. redis_stream_bus.py
# DEFAULT_BLOCK_MS и sse+redis.md §3.9). На каждый XREAD BLOCK после
# таймаута без события генератор шлёт SSE-комментарий ": heartbeat".
# Константа SSE_HEARTBEAT_INTERVAL_SECONDS удалена в Step 4 — heartbeat
# больше НЕ отдельный asyncio.sleep, а side-effect пустого XREAD.

_sse_log = get_logger("sse_generator")


async def _sse_event_stream_generator(
    request: Request,
    user_id: int,
    habit_id: str,
    connection_limiter: SseConnectionLimiter,
    stream_bus: RedisStreamBus,
    last_event_id: str | None = None,
):
    """SSE-генератор с XREAD-циклом (Step 4).

    Поведение:
    1. Сразу шлём `: connected\\n\\n` — клиент видит 200 OK и не висит.
    2. В цикле:
       - Проверяем `request.is_disconnected()` — закрытие EventSource по HTTP.
       - Делаем `XREAD BLOCK 30000 STREAMS sse:user:{u}:{h} <start_id>` через
         `RedisStreamBus.read_blocking`.
         - На `<start_id>` влияют `last_event_id_header` (нативный
           EventSource reconnect — приоритет), `last_event_id` из query
           (ручной reconnect с новым токеном — fallback), иначе `$`
           ("только новые").
         - Поле `last_event_id` в сигнатуре оставлено для совместимости с
           тестами Step 2 (`test_sse_stream_api`); header и query
           обрабатываются ниже в `stream_sse_events`.
       - Для каждой записи: `id: <stream-id>\\nevent: <event>\\ndata: <json>\\n\\n`
         через `sse_formatter.format_event_frame`. `last_id` обновляется
         на последний прочитанный ID для следующей итерации (если
         сценарий с явным ID).
       - Если результат пустой (XREAD вернул None/[] из-за block-timeout) —
         отправляем `: heartbeat\\n\\n`.
    3. Cleanup покрывает ВСЕ пути выхода (аналогично Step 2):
       - is_disconnected() → True → break → finally → release
       - CancelledError (Starlette/uvicorn) → except → finally → release
       - нормальный return → finally → release

    MAXLEN-trimmed Last-Event-ID: если клиент прислал ID старше
    `MAXLEN ~ 1000` (событие уже trimmed), XREAD с этим ID просто
    вернёт пустой результат (согласно Redis semantics — start_id,
    выходящий за пределы, нормализуется к next-available). Цикл
    продолжает ждать новых событий с тем же ID. Клиент в этом случае
    теряет событие, но соединение остаётся живым — `sse+redis.md §3.10`,
    явно подтверждено пользователем как допустимо для MVP.
    """
    stream_key = RedisStreamBus.stream_key(user_id, habit_id)
    # В сигнатуре генератора `last_event_id` теперь — уже-вычисленный
    # стартовый ID (вызывающий код stream_sse_events резолвит header > query
    # > "$" с приоритетом, см. RedisStreamBus.resolve_start_id). Генератор
    # бизнес-логику приоритета не повторяет.
    initial_start_id = last_event_id

    try:
        # Flush заголовков сразу: иначе клиент ждёт первый байт до конца
        # первого XREAD BLOCK (до 30 секунд), что выглядит как "висит".
        yield format_connected_comment()

        # `current_id` обновляется после каждого непустого XREAD. Это
        # гарантирует, что если в первой итерации прочитано несколько
        # событий (COUNT=100), следующая XREAD возьмёт ID последнего
        # — без дырки в потоке.
        current_id: str = initial_start_id

        while True:
            if await request.is_disconnected():
                # Клиент закрыл EventSource по HTTP. Выходим чисто через finally.
                break

            # Один XREAD BLOCK. На пустом результате (таймаут) — heartbeat.
            # На исключении из `read_blocking` — finally освободит слот,
            # цикл прервётся (re-raise снаружи генератора).
            entries = await stream_bus.read_blocking(stream_key, current_id)

            if not entries:
                # Block-таймаут истёк, новых событий нет — heartbeat.
                yield format_heartbeat_comment()
                continue

            for entry_id, fields in entries:
                # Формат (Step 3 зафиксировал): event — имя, payload —
                # уже-сериализованный JSON. Никакой трансформации.
                yield format_event_frame(
                    event_id=entry_id,
                    event_name=fields.get("event", "message"),
                    data_json=fields.get("payload", "{}"),
                )
                current_id = entry_id
    finally:
        # finally покрывает ВСЕ пути выхода из генератора (как в Step 2):
        # - нормальный return / break → finally
        # - asyncio.CancelledError (uvicorn shutdown) → finally
        # - любые Redis-исключения из read_blocking → finally
        # `try/finally` без except'а — finally вызывается на любом
        # exception path, в т.ч. BaseException (CancelledError).
        # Освобождаем слот в Redis. Идемпотентно через clamp-decr.
        # Если Redis временно недоступен — логируем warning, не валим
        # генератор. TTL=180с в connection_limiter страхует от permanent leak.
        try:
            await connection_limiter.release(user_id)
        except Exception as exc:  # noqa: BLE001 — cleanup не должен ронять генератор
            _sse_log.warning(
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
            "Приоритет: header Last-Event-ID (нативный reconnect) выше, "
            "чем query (ручной reconnect). Если оба отсутствуют — "
            "читаются только новые события (`$`)."
        ),
    ),
    last_event_id_header: str | None = Header(
        default=None,
        alias="Last-Event-ID",
        description=(
            "Нативный механизм EventSource reconnect — клиент шлёт ID "
            "последнего полученного события. Приоритет выше query (см. "
            "выше). Используется для resume позиции XREAD."
        ),
    ),
) -> StreamingResponse:
    """SSE-стрим событий по клубу.

    Авторизация: через JWT-токен в query (см. sse_token.py). AuthMiddleware
    НЕ валидирует initData для этого пути (exact-path исключение в
    SSE_AUTH_BYPASS_PATHS), иначе EventSource не сможет передать токен.

    Параметры Last-Event-ID (header) и last_event_id (query) фиксируют
    resume-позицию для reconnect'а. Приоритет:
    `Last-Event-ID` header → `last_event_id` query → `$` (только новые).

    Возвращает text/event-stream. Цикл XREAD BLOCK читает события из
    Redis Stream `sse:user:{user_id}:{habit_id}`. Если за интервал
    `BLOCK` (30с) ничего не пришло — шлёт `: heartbeat\\n\\n`
    SSE-комментарий (держит proxy живым).
    """
    secret = _settings_secret()
    if not secret:
        raise SseNotConfiguredError()

    # Валидация токена. Доменные исключения InvalidServiceTokenError и
    # ServiceTokenExpiredError пробрасываются — глобальный exception
    # handler вернёт 401 + соответствующий code.
    try:
        payload = validate_sse_token(token, secret=secret, expected_habit_id=habit_id)
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

    # Async-Redis: process-level singleton из db/redis_async.py.
    # НЕ `from_url()` per request: каждое `from_url` создаёт новый
    # connection pool, без явного `aclose()` пулы накапливаются при
    # каждом reconnect клиента, и FD лимит упирается задолго до
    # тысяч активных юзеров (post-review блокер Step 4).
    # Singleton создаётся один раз в lifespan startup (main.py),
    # закрывается в shutdown. См. tests/test_sse_stream_api.py —
    # test_async_redis_singleton_is_reused.
    stream_redis = get_async_redis()
    stream_bus = RedisStreamBus(stream_redis)

    # Резолвим start_id с приоритетом header > query > "$". Генератор
    # получает уже вычисленный ID (бизнес-логика — в resolver'е, не в
    # генераторе).
    resolved_start_id = stream_bus.resolve_start_id(
        last_event_id_header=last_event_id_header,
        last_event_id_query=last_event_id,
    )

    return StreamingResponse(
        _sse_event_stream_generator(
            request,
            user_id,
            habit_id,
            connection_limiter,
            stream_bus,
            resolved_start_id,
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
