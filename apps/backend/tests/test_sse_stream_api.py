"""Integration tests for GET /api/v1/events/stream (Step 2).

Покрывает:
- middleware bypass: запросы без X-Telegram-Init-Data НЕ получают 401 missing_init_data
- token validation: missing/invalid/expired/wrong-habit_id → 401, missing params → 422
- 503 при пустом SSE_TOKEN_SECRET
- heartbeat generator: первый yield `: connected\\n\\n`, последующие `: heartbeat\\n\\n`,
  aclose() (имитация client disconnect) → CancelledError ловится, генератор завершается

Замечание про end-to-end стриминг:
ASGI-стриминг не тестируется через httpx/TestClient потому что:
- httpx 0.28 ASGITransport буферизует response body, не доставляет chunks
- TestClient.stream() не отменяет ASGI-таску чисто на exit (генератор с
  while-True блокирует закрытие)
Полное end-to-end стриминг-поведение проверяется руками через curl
на проде в Step 5 (nginx + реальный домен).
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.sse.sse_token import generate_sse_token

# SSE_TOKEN_SECRET из conftest.py — должен совпадать с тем, что читает Settings.
TEST_SSE_SECRET = "test-sse-token-secret"


def _make_token(
    *,
    user_id: int,
    habit_id: str,
    secret: str = TEST_SSE_SECRET,
    now: int | None = None,
) -> str:
    """Сгенерировать валидный SSE-токен для теста."""
    token, _ = generate_sse_token(
        user_id=user_id,
        habit_id=habit_id,
        secret=secret,
        now=now,
    )
    return token


@pytest.fixture
def app_no_db():
    """Минимальный app для тестов SSE-эндпоинта.

    Не требует БД: middleware bypass + token validation + streaming response
    не открывают сессию. Membership lookup в POST /token покрывается в
    test_events_token_api.py.
    """
    os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_sse_"))
    return create_app()


# --- middleware bypass -----------------------------------------------------


class TestSseMiddlewareBypass:
    def test_get_stream_does_not_require_init_data(self, app_no_db: Any) -> None:
        """GET /events/stream без X-Telegram-Init-Data НЕ возвращает
        missing_init_data (этот путь в SSE_AUTH_BYPASS_PATHS).

        Конкретный статус может быть 401 invalid_service_token (токен "x"
        мусорный) — главное что НЕ missing_init_data. Это проверяет, что
        AuthMiddleware действительно пропускает путь /api/v1/events/stream.

        Замечание: end-to-end проверка 200 + headers streaming-ответа
        делается руками через curl в Step 5 (nginx + реальный домен), потому
        что SSE-endpoint никогда не завершает body, и TestClient/httpx
        блокируются на чтении ответа. На этом этапе достаточно убедиться,
        что путь не отбивается auth-middleware (это самая хитрая часть —
        exact-match exclusion, не префикс).
        """
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/stream?habit_id=h&token=x")
        if r.status_code == 401:
            body = r.json()
            assert body.get("code") != "missing_init_data", (
                f"SSE path bypassed AuthMiddleware, не должен возвращать "
                f"missing_init_data: {body}"
            )

    def test_similar_path_under_events_is_not_bypassed(self, app_no_db: Any) -> None:
        """Exact-match (не префикс) — /api/v1/events/history и подобные пути
        ДОЛЖНЫ оставаться под initData-проверкой.

        Если бы middleware использовал startswith('/api/v1/events/'),
        будущий эндпоинт /api/v1/events/history (или любой другой под
        /api/v1/events/*) неожиданно оказался бы без initData-проверки —
        серьёзная дыра в безопасности (SSE_AUTH_BYPASS_PATHS по дизайну
        сдерживает blast-radius до ОДНОГО пути).
        """
        # Проверяем /api/v1/events/history — должен требовать initData
        # (вернёт 401 missing_init_data, не invalid_service_token).
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/history")
        assert r.status_code == 401, f"body: {r.text}"
        assert r.json()["code"] == "missing_init_data", (
            f"/api/v1/events/history должен требовать initData, "
            f"не быть в bypass. Got: {r.json()}"
        )

        # Аналогично /api/v1/events/anything-else — тоже не bypass.
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/foo")
        assert r.status_code == 401
        assert r.json()["code"] == "missing_init_data"

    def test_last_event_id_query_param_accepted_in_signature(self, app_no_db: Any) -> None:
        """last_event_id как опциональный query-параметр зафиксирован в
        сигнатуре эндпоинта с Step 2 (контракт).

        Проверяем через FastAPI dependant.query_params, что параметр
        `last_event_id` действительно присутствует в определении роута и
        опционален (default None). Это гарантирует, что фронт (Step 6) может
        слать параметр уже сейчас, а Step 4 (XREAD) не будет менять
        сигнатуру хендлера.
        """
        from app.api.v1.events import router as events_router

        # Найти роут /events/stream в роутере
        stream_route = None
        for route in events_router.routes:
            if getattr(route, "path", None) == "/events/stream":
                stream_route = route
                break
        assert stream_route is not None, "Route /events/stream не найден"

        # Параметры query через FastAPI dependant (type hints из сигнатуры)
        dependant = stream_route.dependant
        query_param_names = {
            f.name for f in dependant.query_params
        }
        assert "last_event_id" in query_param_names, (
            f"last_event_id должен быть в query_params роута, "
            f"найдено: {query_param_names}"
        )

        # Должен быть опциональным (required=False)
        last_event_field = next(f for f in dependant.query_params if f.name == "last_event_id")
        assert last_event_field.required is False, (
            f"last_event_id должен быть опциональным, "
            f"got required={last_event_field.required}"
        )


# --- token validation ------------------------------------------------------


class TestSseStreamTokenValidation:
    def test_missing_token_returns_422(self, app_no_db: Any) -> None:
        """token обязателен (Query(... min_length=1)) → 422 от Pydantic."""
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/stream?habit_id=h")
        assert r.status_code == 422

    def test_missing_habit_id_returns_422(self, app_no_db: Any) -> None:
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/stream?token=t")
        assert r.status_code == 422

    def test_invalid_token_returns_401(self, app_no_db: Any) -> None:
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/stream?habit_id=h&token=garbage")
        assert r.status_code == 401
        assert r.json()["code"] == "invalid_service_token"

    def test_wrong_secret_returns_401(self, app_no_db: Any) -> None:
        """Токен подписан правильным аудиторией, но другим секретом → 401."""
        habit_id = "h-" + uuid.uuid4().hex[:8]
        token = _make_token(user_id=1, habit_id=habit_id, secret="different-secret")
        with TestClient(app_no_db) as client:
            r = client.get(
                f"/api/v1/events/stream?habit_id={habit_id}&token={token}"
            )
        assert r.status_code == 401
        assert r.json()["code"] == "invalid_service_token"

    def test_expired_token_returns_401(self, app_no_db: Any) -> None:
        habit_id = "h-" + uuid.uuid4().hex[:8]
        # iat=now-300, ttl=60 → exp=now-240, истёк
        token = _make_token(
            user_id=1,
            habit_id=habit_id,
            secret=TEST_SSE_SECRET,
            now=int(time.time()) - 300,
        )
        with TestClient(app_no_db) as client:
            r = client.get(
                f"/api/v1/events/stream?habit_id={habit_id}&token={token}"
            )
        assert r.status_code == 401
        assert r.json()["code"] == "service_token_expired"

    def test_habit_id_mismatch_returns_401(self, app_no_db: Any) -> None:
        """Токен выдан на habit_A, в query пришёл habit_B → 401.

        Это закрывает атаку 'переиграть чужой токен на другой клуб'.
        """
        token = _make_token(user_id=1, habit_id="habit-A")
        with TestClient(app_no_db) as client:
            r = client.get("/api/v1/events/stream?habit_id=habit-B&token=" + token)
        assert r.status_code == 401
        assert r.json()["code"] == "invalid_service_token"

    def test_missing_secret_returns_503(
        self, app_no_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        habit_id = "h-" + uuid.uuid4().hex[:8]
        token = _make_token(user_id=1, habit_id=habit_id)

        monkeypatch.setenv("SSE_TOKEN_SECRET", "")
        get_settings.cache_clear()
        try:
            with TestClient(app_no_db) as client:
                r = client.get(
                    f"/api/v1/events/stream?habit_id={habit_id}&token={token}"
                )
            assert r.status_code == 503, r.text
            assert r.json()["code"] == "sse_not_configured"
        finally:
            monkeypatch.setenv("SSE_TOKEN_SECRET", TEST_SSE_SECRET)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_too_many_sse_connections_returns_429(
        self, app_no_db: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Когда per-user счётчик в Redis исчерпан, эндпоинт возвращает 429
        `too_many_sse_connections`, не пропускает в стрим.

        Интеграционный тест через реальный эндпоинт с подменой Redis-клиента
        на fakeredis (production использует тот же EVAL через lupa).
        """
        import fakeredis.aioredis

        from app.services.sse.connection_limiter import (
            MAX_CONCURRENT_CONNECTIONS_PER_USER,
            SseConnectionLimiter,
        )

        # Подменяем get_redis() на fakeredis — эндпоинт возьмёт наш клиент.
        fake_redis = fakeredis.aioredis.FakeRedis()
        monkeypatch.setattr("app.api.v1.events.get_redis", lambda: fake_redis)

        user_id = 7777
        habit_id = "h-" + uuid.uuid4().hex[:8]
        token = _make_token(user_id=user_id, habit_id=habit_id)

        # Заполняем счётчик до лимита (имитация 5 уже открытых соединений
        # от того же user_id). 6-й acquire должен провалиться.
        limiter = SseConnectionLimiter(fake_redis)  # type: ignore[arg-type]
        for _ in range(MAX_CONCURRENT_CONNECTIONS_PER_USER):
            assert await limiter.try_acquire(user_id) is True
        assert await limiter.try_acquire(user_id) is False  # лимит

        # Делаем запрос — должен быть 429 (НЕ открывать стрим).
        # NB: TestClient + SSE-endpoint = зависает на body, но 429 вернётся
        # сразу (это исключение из handler'а, до StreamingResponse).
        with TestClient(app_no_db) as client:
            r = client.get(
                f"/api/v1/events/stream?habit_id={habit_id}&token={token}"
            )
        assert r.status_code == 429, r.text
        assert r.json()["code"] == "too_many_sse_connections"


# --- heartbeat generator ---------------------------------------------------
#
# Тесты ASGI-стриминга через TestClient/httpx ненадёжны:
# - TestClient.stream() не отменяет ASGI-таску чисто на exit
# - httpx 0.28 ASGITransport буферизует response body
# Поэтому проверяем генератор напрямую — это покрывает логику first-chunk,
# heartbeat, CancelledError-cleanup. End-to-end стриминг будет проверен
# руками через curl на проде (Step 5).


class _FakeRequest:
    """Минимальный stand-in для starlette.Request, нужен только для типа."""

    def __init__(self, disconnect: bool = False) -> None:
        self._disconnect = disconnect

    async def is_disconnected(self) -> bool:
        return self._disconnect


async def _make_fakeredis_with_filled_counter(user_id: int, count: int):
    """Async helper: вернуть (redis, limiter) где счётчик уже заполнен до `count`.

    fakeredis.aioredis поддерживает EVAL/EVALSHA через lupa, но прямой
    sync `redis.incr()` не годится — клиент async. Используем Lua-скрипт
    acquire (тот же что в проде) — проходит через register_script и работает.
    """
    import fakeredis.aioredis

    from app.services.sse.connection_limiter import SseConnectionLimiter

    redis = fakeredis.aioredis.FakeRedis()
    limiter = SseConnectionLimiter(redis)  # type: ignore[arg-type]
    for _ in range(count):
        await limiter.try_acquire(user_id)
    return redis, limiter


class TestSseHeartbeatGenerator:
    @pytest.mark.asyncio
    async def test_first_yield_is_connected_comment(self) -> None:
        """Первый chunk генератора — `: connected\\n\\n`, чтобы клиент сразу
        увидел 200 OK и не висел в ожидании первого байта."""
        from app.api.v1.events import _sse_heartbeat_generator

        redis, limiter = await _make_fakeredis_with_filled_counter(user_id=1, count=0)
        gen = _sse_heartbeat_generator(_FakeRequest(), 1, "h", limiter)
        first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert first == ": connected\n\n"
        # Cleanup: закрываем генератор чисто.
        await gen.aclose()

    @pytest.mark.asyncio
    async def test_subsequent_yields_are_heartbeat(self) -> None:
        """После connected — периодические `: heartbeat\\n\\n`.

        Чтобы не ждать 30с, временно подменяем константу модуля через
        monkeypatch на 0.1с.
        """
        from app.api.v1 import events as events_module
        from app.api.v1.events import _sse_heartbeat_generator

        original = events_module.SSE_HEARTBEAT_INTERVAL_SECONDS
        events_module.SSE_HEARTBEAT_INTERVAL_SECONDS = 0.1
        try:
            redis, limiter = await _make_fakeredis_with_filled_counter(user_id=1, count=0)
            gen = _sse_heartbeat_generator(_FakeRequest(), 1, "h", limiter)
            first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            assert first == ": connected\n\n"

            # Через ~0.1с должен прийти heartbeat.
            second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            assert second == ": heartbeat\n\n"

            # И ещё один — для подтверждения цикличности.
            third = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            assert third == ": heartbeat\n\n"

            await gen.aclose()
        finally:
            events_module.SSE_HEARTBEAT_INTERVAL_SECONDS = original

    @pytest.mark.asyncio
    async def test_client_disconnect_stops_generator_cleanly(self) -> None:
        """aclose() (имитация client disconnect) → CancelledError ловится,
        генератор завершается без утечки исключений."""
        from app.api.v1 import events as events_module
        from app.api.v1.events import _sse_heartbeat_generator

        original = events_module.SSE_HEARTBEAT_INTERVAL_SECONDS
        events_module.SSE_HEARTBEAT_INTERVAL_SECONDS = 0.1
        try:
            redis, limiter = await _make_fakeredis_with_filled_counter(user_id=1, count=0)
            gen = _sse_heartbeat_generator(_FakeRequest(), 1, "h", limiter)
            # Прочитаем один chunk, потом "отключим клиента".
            await asyncio.wait_for(gen.__anext__(), timeout=1.0)

            # aclose() посылает GeneratorExit внутрь, который в asyncio-генераторе
            # становится CancelledError. Наш except CancelledError ловит и возвращает.
            await gen.aclose()

            # Генератор должен быть закрыт. Любой __anext__ после aclose() → StopAsyncIteration.
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()
        finally:
            events_module.SSE_HEARTBEAT_INTERVAL_SECONDS = original

    @pytest.mark.asyncio
    async def test_release_on_aclose_normal_path(self) -> None:
        """Путь 1: aclose() (имитация client disconnect) → finally → release.

        Проверяем, что счётчик в Redis возвращается к 0 после aclose —
        это значит, что finally-блок отработал. Если бы finally не сработал
        (баг в async-generator cleanup), счётчик остался бы = 1 и в проде
        у нас была бы утечка при каждом reconnect.
        """
        from app.api.v1.events import _sse_heartbeat_generator
        from app.services.sse.connection_limiter import KEY_PREFIX

        user_id = 42
        redis, limiter = await _make_fakeredis_with_filled_counter(user_id, count=0)
        gen = _sse_heartbeat_generator(_FakeRequest(), user_id, "h", limiter)
        await asyncio.wait_for(gen.__anext__(), timeout=1.0)

        # Сразу после первого yield счётчик НЕ должен быть 0 — acquire
        # в эндпоинте уже прошёл, но generator ещё не запустил finally.
        # (Мы передали пустой counter в helper, эндпоинт не вызывался —
        # поэтому для чистоты теста: acquire вручную перед yield).
        await limiter.try_acquire(user_id)
        assert int(await redis.get(f"{KEY_PREFIX}{user_id}")) == 1  # type: ignore[arg-type]

        # aclose() → CancelledError → except → return → finally → release
        await gen.aclose()

        # finally отработал: счётчик DECR'нулся обратно к 0.
        count_after = await redis.get(f"{KEY_PREFIX}{user_id}")  # type: ignore[arg-type]
        # После clamp-decr ключ удалён (count=0 → DECR → -1 → DEL).
        assert count_after is None or int(count_after) == 0, (
            f"finally не отработал, count={count_after}"
        )

    @pytest.mark.asyncio
    async def test_release_on_is_disconnected_normal_path(self) -> None:
        """Путь 2: is_disconnected() → True → break → finally → release.

        Этот путь срабатывает когда клиент закрыл EventSource по HTTP
        (не CancelledError). uvicorn worker restart/graceful shutdown
        идут по пути 1 (CancelledError); обычное закрытие вкладки —
        по этому пути. Тест проверяет что finally покрывает оба.
        """
        from app.api.v1 import events as _sse_events_module
        from app.api.v1.events import _sse_heartbeat_generator
        from app.services.sse.connection_limiter import KEY_PREFIX

        original = _sse_events_module.SSE_HEARTBEAT_INTERVAL_SECONDS
        _sse_events_module.SSE_HEARTBEAT_INTERVAL_SECONDS = 0.05
        try:
            user_id = 99
            redis, limiter = await _make_fakeredis_with_filled_counter(user_id, count=0)

            # FakeRequest с disconnect=True — is_disconnected() сразу вернёт True
            fake_req = _FakeRequest(disconnect=True)

            gen = _sse_heartbeat_generator(fake_req, user_id, "h", limiter)
            await limiter.try_acquire(user_id)
            assert int(await redis.get(f"{KEY_PREFIX}{user_id}")) == 1  # type: ignore[arg-type]

            # Генератор должен yield ": connected\n\n", потом проверить
            # is_disconnected() → True → break → finally → release.
            chunks = []
            async for chunk in gen:
                chunks.append(chunk)
                # Генератор должен выйти сам после break, без aclose().
                # Если зависнет — wait_for таймаут.
                if len(chunks) > 5:
                    pytest.fail("generator не вышел после is_disconnected()")

            # Проверяем что первый chunk — ": connected\n\n"
            assert chunks[0] == ": connected\n\n"

            # finally отработал: счётчик освобождён.
            count_after = await redis.get(f"{KEY_PREFIX}{user_id}")  # type: ignore[arg-type]
            assert count_after is None or int(count_after) == 0, (
                f"finally не отработал на is_disconnected-path, count={count_after}"
            )
        finally:
            _sse_events_module.SSE_HEARTBEAT_INTERVAL_SECONDS = original
