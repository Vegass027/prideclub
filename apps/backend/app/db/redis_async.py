"""Async-Redis singleton — компаньон для `redis.py` (sync).

Шаг 4 фичи SSE+Redis Streams (см. `sse+redis.md`): асинхронный
`redis.asyncio.Redis` нужен для `XREAD BLOCK` в `RedisStreamBus` —
sync-клиент из `db/redis.py` НЕ поддерживает async-семантику.

Почему синглтон, а не свежий клиент на запрос:
- `redis.asyncio.from_url()` создаёт **новый connection pool на
  каждый вызов**. SSE-соединения у клиента короткоживущие
  (reconnect на каждый сбой сети в Telegram WebView), `aclose()` на
  них не вызывается — пулы накапливаются, FDs упираются в лимит
  именно того класса, против которого мы всё это проектировали.
- В worker'е тот же подход: `_build_production_cache()` создаёт
  клиента один раз, шарят между тасками.
- Симметрия с `db/redis.py` — sync-клиент уже синглтон, async не
  должен быть хуже.

Lifecycle:
- `get_async_redis()` — lazy singleton через module-level global.
  Идемпотентно: первый вызов создаёт, последующие возвращают тот же
  объект.
- `close_async_redis()` — закрыть пул, сбросить global на None.
  Вызывается в lifespan shutdown (`app/main.py`).
- Lifespan startup дополнительно делает `ping()` — см. main.py —
  чтобы первый SSE-запрос не упирался в холодный connect.

В тестах (TestClient lifespan):
- TestClient при `with TestClient(app) as client:` дёргает startup →
  создаётся singleton → shutdown → закрывается.
- Для unit-тестов генератора (не через эндпоинт) синглтон не
  используется напрямую: тесты передают `stream_bus` явно.
- Для тестов через эндпоинт — `monkeypatch.setattr(
  "app.api.v1.events.get_async_redis", lambda: fake_redis)`. Точно
  так же, как с sync `get_redis` в существующих тестах Step 2.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from redis.asyncio import Redis, from_url

from app.core.config import get_settings

_settings = get_settings()

_async_redis: Redis | None = None


def get_async_redis() -> Redis:
    """Вернуть process-level singleton async-Redis.

    Lazy-init: первый вызов создаёт `Redis` через `from_url`,
    последующие возвращают тот же объект. `redis.asyncio` сам
    пулит TCP-коннекшены внутри клиента — переподключения, retries,
    keep-alive на нём, не на нас.
    """
    global _async_redis
    if _async_redis is None:
        _async_redis = from_url(
            _settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _async_redis


async def close_async_redis() -> None:
    """Закрыть singleton (lifespan shutdown).

    После вызова следующий `get_async_redis()` создаст новый клиент.
    Это нормально для продского shutdown-flow (процесс завершается),
    но в тестах с persistent process (TestClient вне `with`) после
    `close_async_redis()` нужно не забыть снова дёрнуть
    `get_async_redis()` — иначе будет работать с закрытым объектом.
    FastAPI lifespan такие сценарии закрывает сам через порядок
    startup→shutdown в `with TestClient(app) as client`.
    """
    global _async_redis
    if _async_redis is not None:
        await _async_redis.aclose()
        _async_redis = None


async def get_async_redis_dep() -> AsyncIterator[Redis]:
    """FastAPI dependency alias — точно как `get_redis_dep` в redis.py."""
    yield get_async_redis()


__all__ = [
    "get_async_redis",
    "close_async_redis",
    "get_async_redis_dep",
]
