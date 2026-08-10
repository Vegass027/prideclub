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
    streams: dict[str, str],
):
    """SSE-генератор с XREAD-циклом — multiplex (Step 4 + Pravki Item 7).

    Поддерживает два режима через `streams` dict:
    - **Legacy single-stream** (`{user_stream: id}`) — клиент v1 без
      `last_event_id_habit`. Читает только user-stream, поведение как
      до Item 7.
    - **Multiplex** (`{user_stream: id, habit_stream: id}`) — клиент v2
      передал оба параметра. Один XREAD BLOCK на оба стрима, плоский
      output с per-stream cursor.

    Поведение:
    1. Сразу шлём `: connected\\n\\n` — клиент видит 200 OK и не висит.
    2. В цикле:
       - Проверяем `request.is_disconnected()` — закрытие EventSource по HTTP.
       - Делаем `XREAD BLOCK 30000 STREAMS <streams>` через
         `RedisStreamBus.read_blocking_multiplex`.
         - Каждый стрим имеет независимый cursor — последний прочитанный
           ID per-stream обновляется на следующей итерации XREAD.
         - Если результат пустой (XREAD вернул None/[] из-за block-timeout
           на ВСЕ стримы одновременно) — шлём `: heartbeat\\n\\n`.
       - Для каждой записи: `id: <entry_id>\\nevent: <name>\\ndata: <json>\\n\\n`
         через `sse_formatter.format_event_frame`. `stream_name` НЕ
         включается в фрейм (клиент различает по полю `event`: checkin.accepted,
         catch, you_were_caught — см. Pravki Item 9 для UI routing).
    3. Cleanup покрывает ВСЕ пути выхода:
       - is_disconnected() → True → break → finally → release
       - CancelledError (Starlette/uvicorn) → finally → release
       - нормальный return → finally → release

    MAXLEN-trimmed Last-Event-ID: если клиент прислал ID старше
    `MAXLEN ~ 1000` (событие уже trimmed), XREAD с этим ID просто
    вернёт пустой результат (Redis semantics — start_id за пределы
    нормализуется к next-available). Цикл продолжает ждать новых событий.
    Клиент теряет событие, соединение остаётся живым — `sse+redis.md §3.10`.

    Per-stream cursor: `current_ids: dict[str, str]` mutable, обновляется
    на последний прочитанный ID. Для legacy single-stream dict содержит
    ровно одну запись, поведение идентично legacy generator.
    """
    if not streams:
        # Защита от missconfig: пустой dict → legacy single-stream в user-stream.
        # Не должно случаться в production (handler всегда передаёт хотя бы
        # user-stream), но защита для тестов / direct call.
        _sse_log.error(
            "sse_generator_empty_streams",
            extra={"user_id": user_id, "habit_id": habit_id},
        )
        yield format_connected_comment()
        return

    try:
        # Flush заголовков сразу: иначе клиент ждёт первый байт до конца
        # первого XREAD BLOCK (до 30 секунд), что выглядит как "висит".
        yield format_connected_comment()

        # Per-stream cursors. Mutable dict — обновляем на каждый прочитанный
        # entry_id. Для legacy single-stream = один ключ, поведение
        # идентично предыдущей версии.
        current_ids: dict[str, str] = dict(streams)

        while True:
            if await request.is_disconnected():
                # Клиент закрыл EventSource по HTTP. Выходим чисто через finally.
                break

            # Один XREAD BLOCK на ВСЕ стримы одновременно. На пустом результате
            # (таймаут на всех стримах) — heartbeat. На исключении — finally
            # освободит слот, цикл прервётся (re-raise снаружи генератора).
            entries = await stream_bus.read_blocking_multiplex(current_ids)

            if not entries:
                # Block-таймаут истёк, новых событий нет ни на одном стриме —
                # heartbeat (один на итерацию, не per-stream).
                yield format_heartbeat_comment()
                continue

            for stream_name, entry_id, fields in entries:
                # Формат (Step 3 зафиксировал): event — имя, payload —
                # уже-сериализованный JSON. stream_name НЕ включается —
                # клиент различает по полю `event` (Item 9 routing).
                yield format_event_frame(
                    event_id=entry_id,
                    event_name=fields.get("event", "message"),
                    data_json=fields.get("payload", "{}"),
                )
                # Cursor per-stream: обновляем только тот ключ, из которого
                # пришёл event. Для legacy single-stream = один ключ.
                current_ids[stream_name] = entry_id
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
            "Опциональный Redis Streams ID для resume позиции user-stream. "
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
            "выше). Используется для resume позиции XREAD на user-stream."
        ),
    ),
    last_event_id_habit: str | None = Query(
        None,
        description=(
            "Pravki Item 7 (multiplex): опциональный Redis Streams ID "
            "для resume позиции HABIT-stream (sse:habit:{habit_id}). "
            "Если передан — клиент подписан на ОБА стрима (user + habit), "
            "получает broadcast catch_event'ы (Item 8). Если НЕ передан — "
            "только user-stream (legacy v1 fallback, см. sse+redis.md §Z-6.3.5)."
        ),
    ),
) -> StreamingResponse:
    """SSE-стрим событий по клубу (Step 4 + Pravki Item 7 multiplex).

    Авторизация: через JWT-токен в query (см. sse_token.py). AuthMiddleware
    НЕ валидирует initData для этого пути (exact-path исключение в
    SSE_AUTH_BYPASS_PATHS), иначе EventSource не сможет передать токен.

    Параметры Last-Event-ID (header) и last_event_id (query) фиксируют
    resume-позицию для user-stream. last_event_id_habit (query, NEW Item 7)
    фиксирует resume для habit-stream. Приоритет user-stream:
    `Last-Event-ID` header → `last_event_id` query → `$` (только новые).

    Возвращает text/event-stream. Цикл XREAD BLOCK читает события из
    Redis Streams (один или два в зависимости от наличия last_event_id_habit).
    Если за интервал `BLOCK` (30с) ничего не пришло — шлёт `: heartbeat\\n\\n`.

    Multiplex (Item 7):
    - legacy v1 client (last_event_id_habit отсутствует) → подписка только
      на user-stream, поведение как до Item 7. Backward-compat.
    - v2 client (last_event_id_habit задан) → подписка на user + habit,
      один XREAD на оба стрима, per-stream cursor. Получает catch_event
      broadcast'ы.

    Drift detection (Pravki Item 7 review): если клиент уже на resume
    (есть `last_event_id` или `Last-Event-ID` header), но `last_event_id_habit`
    НЕ передан — это сигнал рассинхронизации фронт-кода с v2 backend.
    Логируем warning для диагностики (юзер "иногда не видит catch-events"
    → кто-то должен увидеть лог и понять причину).
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

    # Резолвим start_id для user-stream с приоритетом header > query > "$".
    resolved_start_id_user = stream_bus.resolve_start_id(
        last_event_id_header=last_event_id_header,
        last_event_id_query=last_event_id,
    )

    # Build streams dict (Variant 1 — финальное решение по результатам
    # разведки Item 7): если last_event_id_habit is None, НЕ добавляем
    # habit_stream_key. Это даёт СИЛЬНЫЙ backward-compat: legacy клиент
    # физически не получает catch-event'ы, даже если они произошли между
    # запросами. Тест: test_legacy_client_does_not_receive_habit_catch_events.
    user_stream_key = RedisStreamBus.stream_key(user_id, habit_id)
    habit_stream_key = RedisStreamBus.habit_stream_key(habit_id)
    streams: dict[str, str] = {user_stream_key: resolved_start_id_user}
    if last_event_id_habit is not None:
        streams[habit_stream_key] = last_event_id_habit

    # Pravki Item 7 (review): drift detection для legacy-v1-like клиента.
    # Если клиент уже на resume (есть cursor для user-stream — значит это
    # НЕ первое подключение) и одновременно НЕ передаёт last_event_id_habit —
    # это сигнал рассинхронизации фронт-кода с v2 backend.
    # Без warning'а юзер "иногда" не видит catch-events и никто не понимает
    # почему (catch-event приходит в habit-stream, клиент не подписан).
    is_resume = (
        last_event_id_header is not None or last_event_id is not None
    )
    if is_resume and last_event_id_habit is None:
        # WARNING, не ERROR: это сигнал рассинхронизации (drift bug),
        # но не ломает текущую сессию — клиент продолжает работать в
        # legacy single-stream mode.
        _sse_log.warning(
            "sse_multiplex_drift_detected",
            extra={
                "user_id": user_id,
                "habit_id": habit_id,
                "reason": (
                    "client reconnected with last_event_id but did NOT "
                    "pass last_event_id_habit → habit-stream not subscribed"
                ),
                "subscribed_streams": sorted(streams.keys()),
            },
        )

    return StreamingResponse(
        _sse_event_stream_generator(
            request,
            user_id,
            habit_id,
            connection_limiter,
            stream_bus,
            streams,
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
