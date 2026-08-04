"""Unit tests for SseConnectionLimiter (apps/backend/app/services/sse/connection_limiter.py).

Использует fakeredis.aioredis для in-memory Redis с поддержкой EVAL/EVALSHA
(lupa подключается как dev-dep, см. requirements.txt).

Покрывает:
- Lua-атомарность acquire: INCR + EXPIRE на первом инкременте + rollback при превышении
- TTL ставится только на ПЕРВОМ acquire
- Isolation per user
- release после acquire снова даёт слот
- clamp-decr не уходит ниже 0 (DELETE ключа)
"""
from __future__ import annotations

import pytest

from app.services.sse.connection_limiter import (
    CONNECTION_TTL_SECONDS,
    KEY_PREFIX,
    MAX_CONCURRENT_CONNECTIONS_PER_USER,
    SseConnectionLimiter,
)


def _make_redis():
    import fakeredis.aioredis
    return fakeredis.aioredis.FakeRedis()


def _key(user_id: int) -> str:
    return f"{KEY_PREFIX}{user_id}"


@pytest.mark.asyncio
async def test_acquire_first_slot_sets_ttl() -> None:
    """Первый acquire → INCR=1 + ставит TTL на ключе."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    assert await limiter.try_acquire(user_id=1) is True
    ttl = await redis.ttl(_key(1))  # type: ignore[arg-type]
    assert 0 < ttl <= CONNECTION_TTL_SECONDS


@pytest.mark.asyncio
async def test_acquire_subsequent_slots_do_not_refresh_ttl() -> None:
    """2-й/3-й/... acquire НЕ сбрасывают TTL (иначе счётчик висел бы вечно)."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    await limiter.try_acquire(user_id=1)
    ttl_initial = await redis.ttl(_key(1))  # type: ignore[arg-type]

    await limiter.try_acquire(user_id=1)
    await limiter.try_acquire(user_id=1)
    ttl_after = await redis.ttl(_key(1))  # type: ignore[arg-type]

    # TTL монотонно убывает (или стабилен в ту же секунду), не растёт.
    assert ttl_after <= ttl_initial


@pytest.mark.asyncio
async def test_acquire_up_to_limit_then_rejects() -> None:
    """MAX_CONCURRENT успешных acquire, (MAX+1)-й возвращает False."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    for i in range(MAX_CONCURRENT_CONNECTIONS_PER_USER):
        ok = await limiter.try_acquire(user_id=1)
        assert ok is True, f"acquire #{i + 1} не должен был провалиться"

    # (MAX+1)-й acquire — лимит исчерпан, Lua DECR откатывает счётчик.
    assert await limiter.try_acquire(user_id=1) is False

    # После rollback счётчик не вышел за лимит: остался ровно MAX.
    count = await redis.get(_key(1))  # type: ignore[arg-type]
    assert int(count) == MAX_CONCURRENT_CONNECTIONS_PER_USER


@pytest.mark.asyncio
async def test_release_allows_new_acquire() -> None:
    """После release счётчик DECR'ится → новый acquire снова успешен."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    for _ in range(MAX_CONCURRENT_CONNECTIONS_PER_USER):
        await limiter.try_acquire(user_id=1)
    assert await limiter.try_acquire(user_id=1) is False  # лимит

    await limiter.release(user_id=1)
    assert await limiter.try_acquire(user_id=1) is True  # снова есть место


@pytest.mark.asyncio
async def test_release_is_idempotent() -> None:
    """release без активного acquire (counter=0) → clamp-decr до 0, не уходит в -1."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    await limiter.release(user_id=1)
    # Ключ удалён (clamp-decr), не висит с -1.
    assert await redis.exists(_key(1)) == 0  # type: ignore[arg-type]

    # Повторный release на пустом ключе — тоже без ошибок.
    await limiter.release(user_id=1)
    assert await redis.exists(_key(1)) == 0  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_release_clamp_decr_when_count_went_negative() -> None:
    """Если TTL истёк между acquire и release, DECR уходит в -1 → clamp-decr удаляет ключ."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    # Принудительно истекаем TTL
    await limiter.try_acquire(user_id=1)
    await redis.delete(_key(1))  # type: ignore[arg-type]

    # Теперь release делает DECR на отсутствующем ключе → -1, clamp-decr → DEL.
    await limiter.release(user_id=1)

    # Следующий acquire должен поставить TTL заново (count == 1 после INCR).
    assert await limiter.try_acquire(user_id=1) is True
    ttl = await redis.ttl(_key(1))  # type: ignore[arg-type]
    assert 0 < ttl <= CONNECTION_TTL_SECONDS


@pytest.mark.asyncio
async def test_per_user_isolation() -> None:
    """Лимит per-user: user 1 исчерпал лимит, user 2 не затронут."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    for _ in range(MAX_CONCURRENT_CONNECTIONS_PER_USER):
        await limiter.try_acquire(user_id=1)
    assert await limiter.try_acquire(user_id=1) is False

    # User 2 — отдельный счётчик, должен иметь полный лимит.
    for i in range(MAX_CONCURRENT_CONNECTIONS_PER_USER):
        assert await limiter.try_acquire(user_id=2) is True, (
            f"user 2 acquire #{i + 1} unexpectedly failed"
        )


@pytest.mark.asyncio
async def test_release_decrements_exact_count() -> None:
    """release делает DECR ровно на единицу (а не больше/меньше)."""
    redis = _make_redis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]

    await limiter.try_acquire(user_id=1)
    await limiter.try_acquire(user_id=1)
    await limiter.try_acquire(user_id=1)
    assert int(await redis.get(_key(1))) == 3  # type: ignore[arg-type]

    await limiter.release(user_id=1)
    assert int(await redis.get(_key(1))) == 2  # type: ignore[arg-type]

    await limiter.release(user_id=1)
    assert int(await redis.get(_key(1))) == 1  # type: ignore[arg-type]


def test_constants_documented_in_module() -> None:
    """Защита от случайного изменения N и TTL без обновления docstring/тестов."""
    # Если кто-то поменяет константу без обновления обоснования в docstring —
    # CI упадёт (явное ревью констант).
    assert MAX_CONCURRENT_CONNECTIONS_PER_USER == 5
    assert CONNECTION_TTL_SECONDS == 180
