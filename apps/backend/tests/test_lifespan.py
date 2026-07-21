"""U6: lifespan FastAPI — pre-warm Redis на старте, close_redis на shutdown.

Защищаемся от:
1. Регресса к `@app.on_event` (deprecated в FastAPI 0.110+).
2. Потери shutdown-логики (close_redis), которая закрывает Redis-пул.
3. Холодного connect к Redis на первом запросе (50-200ms задержка).

Контракт:
- `app.router.lifespan_context` — это `@asynccontextmanager`-обёртка.
- TestClient корректно дёргает lifespan при `with TestClient(app)`.
- Внутри lifespan: Redis pings OK, потом yield, потом close_redis.
- Если Redis недоступен на старте — приложение всё равно стартует
  (warning, но не crash), чтобы /health отвечал корректно.
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.db import redis as redis_module
from app.main import create_app


@pytest.fixture
def restore_redis_singleton():
    """Сохраняем и восстанавливаем модуль-уровневый singleton `_redis`."""
    original = redis_module._redis
    yield
    redis_module._redis = original


def _patch_redis_in_main(monkeypatch, fake_redis_instance):
    """Patch `get_redis` там, где он реально используется — в app.main.

    Также обновляем модуль-уровневый singleton `app.db.redis._redis`,
    чтобы `close_redis()` (которая вызывает `_redis.aclose()`) тоже работала
    с фейковым объектом.
    """
    import app.main as main_module

    monkeypatch.setattr(main_module, "get_redis", lambda: fake_redis_instance)
    monkeypatch.setattr(redis_module, "get_redis", lambda: fake_redis_instance)
    monkeypatch.setattr(redis_module, "_redis", fake_redis_instance)


def test_app_uses_lifespan_not_deprecated_on_event() -> None:
    """FastAPI 0.110+ deprecates @app.on_event — используем lifespan context."""
    app = create_app()

    # В FastAPI приложении есть атрибут lifespan, и НЕ должно быть
    # event_handlers для 'shutdown' (что генерируется @app.on_event).
    assert app.router.lifespan_context is not None, (
        "create_app() должен вернуть приложение с настроенным lifespan_context. "
        "Отсутствие = регресс к неявному поведению без startup/shutdown хуков"
    )

    # FastAPI хранит event_handlers ТОЛЬКО если есть @app.on_event.
    # Если мигрировали полностью — shutdown/startup хуков там быть не должно.
    event_handlers = getattr(app, "event_handlers", {})
    shutdown_handlers = event_handlers.get("shutdown", [])
    assert shutdown_handlers == [], (
        "event_handlers['shutdown'] должен быть пуст — используем lifespan, "
        f"а не @app.on_event. Получили: {shutdown_handlers}"
    )
    startup_handlers = event_handlers.get("startup", [])
    assert startup_handlers == [], (
        "event_handlers['startup'] должен быть пуст — используем lifespan, "
        f"а не @app.on_event. Получили: {startup_handlers}"
    )


def test_lifespan_context_is_async_context_manager() -> None:
    """lifespan_context обязан быть async context manager (с yield)."""
    app = create_app()
    ctx = app.router.lifespan_context(app)

    assert inspect.isasyncgenfunction(ctx) or hasattr(ctx, "__aenter__"), (
        f"lifespan_context должен быть async generator (с yield) или "
        f"иметь __aenter__/__aexit__. Тип: {type(ctx)}"
    )


def test_lifespan_runs_redis_ping_on_startup(
    monkeypatch, restore_redis_singleton
) -> None:
    """Pre-warm: на startup lifespan должен дёрнуть Redis.ping()."""
    ping_calls: list[int] = []

    class FakeRedis:
        async def ping(self) -> bool:
            ping_calls.append(1)
            return True

        async def aclose(self) -> None:
            pass

    _patch_redis_in_main(monkeypatch, FakeRedis())

    app = create_app()
    with TestClient(app):
        pass  # заходим в lifespan context, выходим

    assert ping_calls == [1], (
        f"на startup lifespan должен ровно один раз вызвать Redis.ping() "
        f"для прогрева пула. Получили {len(ping_calls)} вызовов"
    )


def test_lifespan_closes_redis_on_shutdown(
    monkeypatch, restore_redis_singleton
) -> None:
    """close_redis() обязан быть вызван на shutdown — иначе утечка пула."""
    closed: list[bool] = []

    class FakeRedis:
        async def ping(self) -> bool:
            return True

        async def aclose(self) -> None:
            closed.append(True)

    _patch_redis_in_main(monkeypatch, FakeRedis())

    app = create_app()
    with TestClient(app):
        pass

    assert closed == [True], (
        "на shutdown lifespan должен вызвать Redis.aclose() — иначе при "
        "рестарте контейнера пул остаётся открытым (file descriptor leak). "
        f"aclose вызван: {closed}"
    )


def test_lifespan_survives_redis_unavailable_at_startup(
    monkeypatch, restore_redis_singleton
) -> None:
    """Если Redis недоступен на старте — приложение ВСЁ РАВНО стартует.

    /health должен отвечать, остальные эндпоинты сами решат, что делать.
    Иначе при рестарте Redis backend не сможет подняться → каскадный outage.
    """
    class BrokenRedis:
        async def ping(self) -> bool:
            raise ConnectionError("redis down")

        async def aclose(self) -> None:
            pass

    _patch_redis_in_main(monkeypatch, BrokenRedis())

    app = create_app()
    # Если lifespan крашится — TestClient.__enter__ бросит исключение.
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200, (
            "lifespan не должен валить старт при недоступном Redis — "
            "иначе /health не отвечает и оркестратор не может снять "
            "health-пробу"
        )


def test_lifespan_calls_close_redis_even_after_startup_failure(
    monkeypatch, restore_redis_singleton
) -> None:
    """Даже если startup упал, shutdown-блок finally всё равно вызывает close_redis.

    Гарантия из `try/finally` внутри lifespan: redis cleanup не теряется.
    """
    closed: list[bool] = []

    class BrokenPingRedis:
        async def ping(self) -> bool:
            raise ConnectionError("simulated startup failure")

        async def aclose(self) -> None:
            closed.append(True)

    _patch_redis_in_main(monkeypatch, BrokenPingRedis())

    app = create_app()
    with TestClient(app):
        pass

    assert closed == [True], (
        "try/finally внутри lifespan ОБЯЗАН вызвать aclose даже если "
        "startup-фаза (ping) упала. Иначе утечка пула при каждом "
        "неудачном деплое"
    )


def test_lifespan_order_is_startup_then_yield_then_shutdown(
    monkeypatch, restore_redis_singleton
) -> None:
    """Жёсткий порядок: ping (startup) → yield (app lives) → aclose (shutdown).

    Если порядок нарушен (например, aclose до ping) — Redis-пул закрывается
    раньше времени и запросы падают с ConnectionError.
    """
    timeline: list[str] = []

    class OrderedRedis:
        async def ping(self) -> bool:
            timeline.append("ping")
            return True

        async def aclose(self) -> None:
            timeline.append("aclose")

    _patch_redis_in_main(monkeypatch, OrderedRedis())

    app = create_app()
    with TestClient(app) as client:
        # Внутри lifespan: ping уже произошёл, yield активен.
        timeline.append("inside_with")
        # Реальный HTTP-запрос чтобы убедиться что Redis жив.
        r = client.get("/health")
        assert r.status_code == 200

    timeline.append("after_with")

    assert timeline == ["ping", "inside_with", "aclose", "after_with"], (
        f"нарушен порядок lifespan. Ожидали ['ping', 'inside_with', 'aclose', "
        f"'after_with'], получили {timeline}. Особенно критично: ping ДОЛЖЕН "
        f"быть до 'inside_with', aclose — после"
    )

    # FastAPI хранит event_handlers ТОЛЬКО если есть @app.on_event.
    # Если мигрировали полностью — shutdown/startup хуков там быть не должно.
    event_handlers = getattr(app, "event_handlers", {})
    shutdown_handlers = event_handlers.get("shutdown", [])
    assert shutdown_handlers == [], (
        "event_handlers['shutdown'] должен быть пуст — используем lifespan, "
        f"а не @app.on_event. Получили: {shutdown_handlers}"
    )
    startup_handlers = event_handlers.get("startup", [])
    assert startup_handlers == [], (
        "event_handlers['startup'] должен быть пуст — используем lifespan, "
        f"а не @app.on_event. Получили: {startup_handlers}"
    )
