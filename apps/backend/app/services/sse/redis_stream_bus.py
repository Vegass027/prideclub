"""Абстракция чтения Redis Streams для SSE-эндпоинта (Step 4).

Шаг 4 плана `sse+redis.md` (редакция 6). Это компаньон для
`worker.services.event_publisher` (Step 3) — там пишут XADD, тут читаем
XREAD BLOCK. Контракт стрима (`sse:user:{u}:{h}`, MAXLEN ~ 1000, поля
`event/habit_id/user_id/occurred_at/payload`) зафиксирован в Step 3.

Почему отдельный async-клиент, а не `db/redis.get_redis()`:
- `db/redis.py` создаёт sync `redis.Redis` (decode_responses=True) — там
  HTTP rate-limiter, `SseConnectionLimiter` (Lua через register_script),
  catch_rate_limiter. Это всё sync-API или свои async-обёртки.
- Нам нужен настоящий `redis.asyncio.Redis` через `redis.asyncio.from_url`
  с теми же параметрами (encoding, decode_responses) — для `xread(streams,
  block=…, count=…)` с async-семантикой.
- Не трогаем синглтон `db/redis.py` — он используется в catch_rate_limiter
  и connection_limiter, менять там что-то ради SSE-консьюмера нельзя.

DI через конструктор: `RedisStreamBus(redis: Redis)`. В проде сюда
инжектится свежий async-клиент (один на процесс), в тестах —
`fakeredis.aioredis.FakeRedis(decode_responses=True)`.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger("sse.redis_stream_bus")


# `XREAD BLOCK 30000` — 30 секунд. Подтверждено пользователем (Q3,
# sse+redis.md §3.9). Достаточно часто чтобы nginx `proxy_read_timeout`
# (после правки 3600s на хосте) не отрезал соединение, не слишком часто
# для шума.
DEFAULT_BLOCK_MS = 30_000

# Батч в одной XREAD-итерации. Небольшое число — N событий между
# heartbeat'ами на одном клиенте могут скопиться только если publish-цикл
# сзади работает быстрее XREAD'а; в норме stream читается по одному событию.
DEFAULT_COUNT = 100

START_ID_ONLY_NEW = "$"  # noqa: S105 — public Redis sentinel, не секрет


class RedisStreamBus:
    """Async-читалка Redis Streams для SSE-генератора.

    Контракт: один экземпляр на SSE-соединение. Не thread-safe в широком
    смысле, но в async-контексте (один uvicorn worker, --workers 2) каждый
    генератор держит свой bus — гонок нет.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        block_ms: int = DEFAULT_BLOCK_MS,
        count: int = DEFAULT_COUNT,
    ) -> None:
        self._redis = redis
        self._block_ms = block_ms
        self._count = count

    @staticmethod
    def stream_key(user_id: int, habit_id: str) -> str:
        """Ключ Redis Stream — идентичен `EventPublisher.stream_key` (Step 3).

        Хранится здесь (в дополнение к `worker.services.event_publisher`)
        потому что:
        - producer и consumer живут в разных image-ах (worker Python vs
          backend Python) — общий `packages/`-модуль не используется.
        - Формат ключа зафиксирован в `sse+redis.md §2.3`; расхождение
          сломает Stream consumer.
        - Тест `test_sse_stream_api` и `test_event_publisher` оба
          используют одинаковый ключ — это контракт через строки,
          не через импорт.
        """
        return f"sse:user:{user_id}:{habit_id}"

    @staticmethod
    def habit_stream_key(habit_id: str) -> str:
        """Ключ Redis Stream habit-broadcast — идентичен `EventPublisher.habit_stream_key` (Item 6).

        Producer (`publish_to_habit` в worker) пишет в этот ключ,
        consumer (`read_blocking_multiplex` в backend) читает. Контракт
        через строки (оба image содержат одинаковую реализацию), не
        через shared-пакет — аналогично `stream_key` выше.

        Добавлен в Item 7 (SSE multiplex). Legacy clients (v1) не
        используют этот стрим (см. handler `stream_sse_events`,
        Variant 1 — habit-stream подписывается только при
        `last_event_id_habit is not None`).
        """
        return f"sse:habit:{habit_id}"

    @staticmethod
    def resolve_start_id(
        *,
        last_event_id_header: str | None,
        last_event_id_query: str | None,
    ) -> str:
        """Определить стартовый ID для XREAD.

        Приоритет (sse+redis.md §2.4):
        1. Header `Last-Event-ID` (нативный EventSource reconnect) — если есть.
        2. Query `last_event_id` (ручной reconnect с новым токеном) — если нет header.
        3. `$` (только новые) — если ни того, ни другого.

        Чистая функция (без зависимости от инстанса), тестируется напрямую
        без мока Redis. NB: `redis-py` принимает как исторический ID
        (например, `1785858587616-0`), так и `$`. Передаём строку as-is.
        """
        if last_event_id_header:
            return last_event_id_header
        if last_event_id_query:
            return last_event_id_query
        return START_ID_ONLY_NEW

    async def read_blocking(
        self,
        stream_key: str,
        start_id: str,
    ) -> list[tuple[str, dict[str, str]]]:
        """Один шаг XREAD BLOCK.

        Возвращает плоский список `(entry_id, fields)` для `stream_key`,
        ОТНОСИТЕЛЬНО start_id (exclusive — entry с id > start_id).

        Returns:
            Список `(entry_id, fields_dict)` (fields — dict строка→строка,
            потому что клиент с `decode_responses=True`). Пустой список —
            если XREAD вернул `None` (block timeout) или пустой результат.
            Исключения НЕ глотаем — пробрасываем наверх; генератор
            обрабатывает их в общем try/except (cleanup → release).

        Семантика "пустой список на любом пустом результате":
        - блок истёк без новых событий → `redis-py` возвращает `None`
          (реальный Redis) или `[]` (fakeredis с `$` без новых событий)
          — нормализуем до `[]` для удобства вызывающего кода
          (`if not entries: yield ": heartbeat"`).
        - стрим не существует (`XREAD STREAMS nonexistent $`) →
          redis-py возвращает `None`/`[]` — то же поведение.
        """
        try:
            result = await self._redis.xread(
                {stream_key: start_id},
                count=self._count,
                block=self._block_ms,
            )
        except Exception as exc:  # noqa: BLE001
            # Не глотаем молча — генератор ловит снаружи и закрывает
            # соединение. Здесь логируем для диагностики XREAD-фейлов.
            logger.warning(
                "sse_xread_failed",
                extra={
                    "stream_key": stream_key,
                    "start_id": start_id,
                    "err": type(exc).__name__,
                },
            )
            raise

        if not result:
            return []

        # result: list[[stream_name, [(entry_id, fields), ...]]]
        # У нас в streams всегда ровно один stream — берём первый элемент.
        _stream_name, entries = result[0]
        return [(entry_id, fields) for entry_id, fields in entries]

    async def read_blocking_multiplex(
        self,
        streams: dict[str, str],
    ) -> list[tuple[str, str, dict[str, str]]]:
        """Multiplex: один XREAD BLOCK на НЕСКОЛЬКО стримов с независимыми
        курсорами (Pravki-deposit-sse.md §Z-6.7, Item 7).

        Args:
            streams: `{stream_key: start_id}` — один или более ключей Redis
                Streams с независимыми стартовыми позициями. Redis
                поддерживает `XREAD STREAMS k1 k2 id1 id2` нативно (один
                round-trip, один блок на все стримы одновременно).

        Returns:
            Плоский список `(stream_name, entry_id, fields)` — order НЕ
            гарантирован между стримами (Redis spec). Генератор
            обновляет per-stream cursor по `stream_name`.

            Пустой список если block-таймаут на ВСЕ стримы одновременно
            (любое событие на ЛЮБОМ стриме снимает блок и попадает в
            ответ).

        Backward-compat: существующий `read_blocking` не тронут. Legacy
        клиенты (v1, без `last_event_id_habit`) вызывают `read_blocking`
        через single-stream dict `{user_stream: id}` в generator'е — Item 7
        не заставляет всех клиентов переходить на multiplex.

        Raises:
            Любые исключения redis (ConnectionError, timeout и т.п.)
            пробрасываются — generator ловит в finally (cleanup → release).
            Здесь логируем `sse_xread_multiplex_failed` с per-stream info
            для диагностики.

        Note:
            При пустом `streams={}` метод сразу возвращает `[]` без обращения
            к Redis — защита от race condition / missconfig.
        """
        if not streams:
            return []

        try:
            result = await self._redis.xread(
                streams,
                count=self._count,
                block=self._block_ms,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sse_xread_multiplex_failed",
                extra={
                    "stream_keys": sorted(streams.keys()),
                    "err": type(exc).__name__,
                },
            )
            raise

        if not result:
            return []

        # Redis spec: XREAD STREAMS возвращает
        # `[(stream_name, [(entry_id, fields), ...]), ...]`.
        # Flatten в плоский список `(stream_name, entry_id, fields)`.
        out: list[tuple[str, str, dict[str, str]]] = []
        for stream_name, entries in result:
            for entry_id, fields in entries:
                out.append((stream_name, entry_id, fields))
        return out


__all__ = [
    "DEFAULT_BLOCK_MS",
    "DEFAULT_COUNT",
    "START_ID_ONLY_NEW",
    "RedisStreamBus",
]
