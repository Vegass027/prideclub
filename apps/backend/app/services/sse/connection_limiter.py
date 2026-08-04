"""Per-user concurrent SSE connection limiter.

Защита от DoS через replayable SSE-токены:
- POST /events/stream/token остаётся под обычным rate-limit (60/min/user
  в RateLimitMiddleware).
- GET /events/stream авторизуется токеном (TTL 60с, не одноразовый —
  см. sse+redis.md §3.11). Один валидный токен может быть использован
  для открытия неограниченного числа соединений в течение 60-секундного
  окна.
- Без лимита: атакующий с curl или бажный reconnect-цикл на фронте
  (manual reconnect-loop в useTodayStream.ts) может исчерпать file
  descriptors backend'а.

Реализация: per-user счётчик в Redis DB 0 с TTL-страховкой от утечки при
крэшах. Atomic через Lua (как RedisCatchRateLimiter в catch_rate_limiter.py)
— иначе при гонке INCR+check+rollback в Python даёт окно для overshoot:
два параллельных запроса от одного юзера могут оба проскочить лимит до
проверки, прежде чем один откатится. Lua делает INCR + проверку + rollback
в одном atomic-блоке на стороне Redis.

Лимит MAX_CONCURRENT_CONNECTIONS_PER_USER=5: типичный юзер держит
1 EventSource на активной вкладке. У активного юзера может быть
3-4 клуба одновременно (sse:user:{u}:{habit_id} — по одному стриму на
каждый habit) + один дубль от reconnect-race на фронте (предыдущий
EventSource ещё не успел закрыться, а новый уже открывается). 5 —
небольшой запас над типичным использованием, не безлимит.

CONNECTION_TTL_SECONDS=180: страховка от permanent leak если worker kill -9
без cleanup. При graceful shutdown uvicorn соединения закрываются за ~30с
(uvicorn graceful_timeout), в норме cleanup проходит через DECR в finally
генератора. TTL = 3× максимального ожидаемого времени жизни — если за
3 минуты счётчик не DECR'нулся, ключ сам истечёт.
"""
from __future__ import annotations

from redis.asyncio import Redis

CONNECTION_TTL_SECONDS = 180

# Per-user лимит одновременных SSE-соединений. Обоснование — см. docstring модуля.
MAX_CONCURRENT_CONNECTIONS_PER_USER = 5

KEY_PREFIX = "sse:conn:"


# Атомарный check-and-incr. INCR + на ПЕРВОМ инкременте ставим TTL
# (чтобы ключ не висел вечно при потере DECR). Если пост-INCR значение
# > MAX — откатываем (DECR) и возвращаем -1 как сигнал отказа.
# Без rollback параллельные запросы могли бы оба проскочить лимит до проверки.
_ACQUIRE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if count > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return -1
end
return count
"""

# Атомарный clamp-decr. Если DECR уводит счётчик ниже 0 (например, TTL
# истёк между acquire и release) — удаляем ключ. Иначе следующий INCR
# начнёт с 0 и не поставит TTL (count==1 не выполнится).
_RELEASE_SCRIPT = """
local count = redis.call('DECR', KEYS[1])
if count < 0 then
    redis.call('DEL', KEYS[1])
    return 0
end
return count
"""


class SseConnectionLimiter:
    """Per-user concurrent SSE connection limiter (Redis-backed)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._acquire_script = self._redis.register_script(_ACQUIRE_SCRIPT)
        self._release_script = self._redis.register_script(_RELEASE_SCRIPT)

    @staticmethod
    def _key(user_id: int) -> str:
        return f"{KEY_PREFIX}{user_id}"

    async def try_acquire(self, user_id: int) -> bool:
        """Попытаться занять слот. True = успешно, False = лимит исчерпан.

        Не бросает исключение — handler сам решает что делать при False
        (обычно 429 too_many_sse_connections).
        """
        result = await self._acquire_script(
            keys=[self._key(user_id)],
            args=[MAX_CONCURRENT_CONNECTIONS_PER_USER, CONNECTION_TTL_SECONDS],
        )
        return int(result) != -1

    async def release(self, user_id: int) -> None:
        """Освободить слот. Идемпотентно (повторный release безопасен).

        Используется в finally-блоке SSE-генератора — должен быть
        идемпотентным, потому что CancelledError/break/return пути
        могут пересекаться.
        """
        await self._release_script(keys=[self._key(user_id)])
