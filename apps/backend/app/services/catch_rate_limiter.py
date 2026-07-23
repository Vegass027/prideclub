from __future__ import annotations

from redis.asyncio import Redis

from app.core.constants import PenaltyConfig


class RedisCatchRateLimiter:
    """Atomic INCR + EXPIRE, 10 запросов / 10 секунд.

    Lua-скрипт гарантирует атомарность: иначе при гонке можно получить
    двойной INCR без EXPIRE.
    """

    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then
        redis.call('EXPIRE', KEYS[1], ARGV[1])
    end
    return count
    """

    PREFIX = "catch_rate:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._max, self._ttl_seconds = _parse(PenaltyConfig.RATE_LIMIT_CATCH)
        self._script = self._redis.register_script(self._SCRIPT)

    @staticmethod
    def _key(user_id: int) -> str:
        return f"{RedisCatchRateLimiter.PREFIX}{user_id}"

    async def incr_catch(self, catcher_user_id: int) -> int:
        result = await self._script(
            keys=[self._key(catcher_user_id)],
            args=[self._ttl_seconds],
        )
        return int(result)


def _parse(spec: str) -> tuple[int, int]:
    count, _, ttl = spec.partition("/")
    if ttl.endswith("s"):
        return int(count), int(ttl[:-1])
    if ttl.endswith("m"):
        return int(count), int(ttl[:-1]) * 60
    raise ValueError(f"Bad rate-limit spec: {spec}")