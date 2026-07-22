"""HTTP rate limiter поверх Redis.

Используется в middleware для общего ограничения запросов на пользователя
(/api/v1/*) и сервисный caller (/internal/*). Атомарность через Lua-script
по аналогии с RedisCatchRateLimiter.
"""
from __future__ import annotations

from redis.asyncio import Redis

from app.core.utils import parse_rate_limit_spec


class RedisHttpRateLimiter:
    """Sliding-bucket через fixed-window INCR+EXPIRE (как у catch-rate-limiter).

    Lua-скрипт гарантирует что TTL выставляется ровно один раз, при первом INCR —
    иначе при гонке INCR-EXPIRE-INCR ключ может истечьнуть раньше чем надо.
    """

    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """

    def __init__(self, redis: Redis, max_requests: int, window_seconds: int, prefix: str) -> None:
        self._redis = redis
        self._max = max_requests
        self._window = window_seconds
        self._prefix = prefix
        self._script = self._redis.register_script(self._SCRIPT)

    def _key(self, subject: str) -> str:
        return f"{self._prefix}{subject}"

    async def check(self, subject: str) -> tuple[bool, int, int]:
        """Возвращает (allowed, current_count, max)."""
        result = await self._script(
            keys=[self._key(subject)],
            args=[self._window],
        )
        count = int(result)
        return count <= self._max, count, self._max


def make_api_v1_limiter(redis: Redis) -> RedisHttpRateLimiter:
    from app.core.constants import HttpRateLimitConfig

    max_n, window = parse_rate_limit_spec(HttpRateLimitConfig.RATE_LIMIT_API_V1)
    return RedisHttpRateLimiter(redis, max_n, window, prefix="http_rate:api:")


def make_internal_limiter(redis: Redis) -> RedisHttpRateLimiter:
    from app.core.constants import HttpRateLimitConfig

    max_n, window = parse_rate_limit_spec(HttpRateLimitConfig.RATE_LIMIT_INTERNAL)
    return RedisHttpRateLimiter(redis, max_n, window, prefix="http_rate:int:")


__all__ = [
    "RedisHttpRateLimiter",
    "make_api_v1_limiter",
    "make_internal_limiter",
]
