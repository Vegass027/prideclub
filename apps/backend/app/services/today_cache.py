from __future__ import annotations

from redis.asyncio import Redis


class RedisTodayCache:
    """Кэш статуса "сегодня" в Redis.

    Ключи: today:{habit_id}:{membership_id}.
    Инвалидируется при успешном чек-ине.
    """

    PREFIX = "today:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _key(habit_id: str, membership_id: str) -> str:
        return f"{RedisTodayCache.PREFIX}{habit_id}:{membership_id}"

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None:
        await self._redis.delete(self._key(habit_id, membership_id))