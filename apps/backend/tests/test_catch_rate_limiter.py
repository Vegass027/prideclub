from __future__ import annotations

import pytest

from app.core.constants import PenaltyConfig
from app.services.catch_rate_limiter import RedisCatchRateLimiter, _parse


def test_parse_seconds() -> None:
    n, ttl = _parse("10/10s")
    assert (n, ttl) == (10, 10)


def test_parse_minutes() -> None:
    n, ttl = _parse("5/1m")
    assert (n, ttl) == (5, 60)


def test_parse_invalid() -> None:
    with pytest.raises(ValueError):
        _parse("10/10")


@pytest.mark.asyncio
async def test_lua_atomic_incr_and_expire() -> None:
    """Lua-скрипт должен: 1) атомарно инкрементировать, 2) ставить TTL на ПЕРВОМ инкременте."""
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis()
    limiter = RedisCatchRateLimiter(redis)  # type: ignore[arg-type]

    # Первый INCR → ставит TTL.
    assert await limiter.incr_catch(catcher_user_id=1) == 1
    ttl = await redis.ttl(RedisCatchRateLimiter._key(1))  # type: ignore[arg-type]
    assert 0 < ttl <= 10  # RATE_LIMIT_CATCH = "10/10s"

    # Второй/третий INCR — TTL НЕ сбрасывается.
    assert await limiter.incr_catch(catcher_user_id=1) == 2
    assert await limiter.incr_catch(catcher_user_id=1) == 3
    ttl2 = await redis.ttl(RedisCatchRateLimiter._key(1))  # type: ignore[arg-type]
    assert 0 < ttl2 <= ttl  # TTL монотонно убывает (или стабилен в одну секунду)


@pytest.mark.asyncio
async def test_rate_limit_threshold_per_user_isolated() -> None:
    """Лимит на одного пользователя не должен протекать на другого."""
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis()
    limiter = RedisCatchRateLimiter(redis)  # type: ignore[arg-type]

    # user 1: 5 раз
    for _ in range(5):
        await limiter.incr_catch(catcher_user_id=1)
    # user 2: 1 раз — должен быть отдельным счётчиком
    assert await limiter.incr_catch(catcher_user_id=2) == 1


def test_constants_have_rate_limit() -> None:
    """Антифрод-инвариант: rate limit на catch задан и валиден."""
    n, ttl = _parse(PenaltyConfig.RATE_LIMIT_CATCH)
    assert n > 0
    assert ttl > 0
    # По docs: 10 запросов / 10 секунд.
    assert n == 10
    assert ttl == 10
