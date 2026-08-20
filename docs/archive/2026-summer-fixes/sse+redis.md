Применяю две доработки и закрываю Q3-Q7. Ниже — финальная редакция плана целиком.

---

# План: real-time обновление статуса чек-ина через SSE + Redis Streams

> Документ только для обсуждения. Правки ниже — только в самом плане-документе,
> отражают реальное состояние после применения Step 1 + Step 2 + двух fix-up'ов + Step 3 + Step 4 + Step 5 + Step 6 + фикс `d30832a`.
> Snapshot разведки: 2026-08-04 (актуализировано после Step 6), на основе `feature/topic-scoped-checkin` (`7ada2ad` → `d30832a`).
> Редакция 9 (актуализация): **Step 5 + Step 6 реализованы локально и в feature-ветке**:
> nginx-блок написан в репо (`900ef4f`), применён вручную на проде
> (`169.58.52.78`, `/etc/nginx/sites-enabled/habit-club`, бэкап в
> `/var/backups/nginx/habit-club.bak.20260804_1823`, `nginx -t` ОК,
> debug-тест с `return 418` подтвердил exact-match).
> Frontend Step 6 (`5d8c6e6`) + фикс mount-invalidate (`d30832a`)
> реализованы в `feature/topic-scoped-checkin`, прогоняются локально:
> 11/11 vitest, `tsc --noEmit` чистый, eslint чистый, `npm run build`
> 185 модулей в 1.34 с (bundle `index-*.js` 309.93 KB / 101.85 KB gzip
> без изменений от baseline).
> **Но Steps 1-4 на прод НЕ задеплоены** (прод работает на `main` `bd9fd76`,
> `apps/backend/app/api/v1/events.py` отсутствует в контейнере,
> `SSE_TOKEN_SECRET` нет в env) → SSE-функционал в Mini App **не
> работает** до тех пор, пока Steps 1-4 не будут задеплоены
> (merge `feature/topic-scoped-checkin` → main + rsync + build +
> `SSE_TOKEN_SECRET` в `/app/.env`). nginx-блок при этом безвреден —
> если роута нет, поведение для клиента идентично исходному (nginx
> просто проксирует, а backend отвечает 401/404 как и без блока).
> Правки редакции 8 сохранены: Steps 1+2+3+4+5+6 реализованы, два
> fix-up'а, manual reconnect-loop, exact-path middleware,
> membership-check при выдаче токена, Q3-Q7 закрыты.

## Статус реализации (snapshot 2026-08-04 после Step 6)

| Шаг | Описание | Статус | Коммиты |
|---|---|---|---|
| test-infra | `STATIC_DIR` фикс для локального запуска тестов | ✅ done | `1abd331` |
| **Step 1** | SSE token endpoint + JWT helpers | ✅ done | `c836542` |
| **Step 2** | GET SSE stream + middleware bypass | ✅ done | `9d5b374` |
| fix-up 1 | `last_event_id` query param в контракте + тест exact-match bypass | ✅ done | `a0217ec` |
| fix-up 2 | Per-user SSE concurrency limit (Lua atomic) | ✅ done | `ec60c0f` |
| **Step 3** | Worker `event_publisher.py` + правка `process_checkin.py` | ✅ done | `11edb14`, `e5cc8e0` |
| **Step 4** | Backend `redis_stream_bus.py` + XREAD в SSE endpoint + async-Redis singleton | ✅ done | `7ada2ad` |
| **Step 5** | Nginx `proxy_buffering off` + `proxy_read_timeout 3600s` + `access_log off` для `location = /api/v1/events/stream` | ✅ done (nginx + repo) | `900ef4f` + ручное применение `2026-08-04` |
| **Step 6** | Frontend: `useTodayStream` + `sseToken` API + `streamController` (DI, без React) + 11 vitest unit | ✅ done | `5d8c6e6`, `d30832a` |
| Step 7 | Полный набор тестов + деплой | ⏳ pending | — |
| Step 8 | Деплой + ритуал поддержания доков | ⏳ pending | — |

### ⚠️ Расхождение с реальностью (snapshot 2026-08-04 после Step 6)

| Что | Где | Статус |
|---|---|---|
| Steps 1-4 (SSE-роут, middleware bypass, XREAD pipeline, worker event_publisher) задеплоены на прод? | `apps/backend/app/api/v1/events.py`, `core/middleware.py`, `worker/tasks/process_checkin.py` в контейнере `habit-backend` (образ `d2e8c35817ea…`) | ❌ **НЕТ** — прод работает на `main` (`bd9fd76`). `events.py` отсутствует, `SSE_AUTH_BYPASS_PATHS` нет в `middleware.py`, `SSE_TOKEN_SECRET` нет в env. **SSE в Mini App не работает** до деплоя Steps 1-4. |
| Step 5 nginx-блок применён на проде? | `/etc/nginx/sites-enabled/habit-club` на `169.58.52.78` | ✅ ДА — блок вставлен перед `location /api/` в server `app.prideclub.fun`, `nginx -t` ОК, debug-тест с `return 418` подтвердил exact-match. Бэкап `/var/backups/nginx/habit-club.bak.20260804_1823`. |
| Step 6 frontend в feature-ветке, не на проде? | `apps/frontend/src/shared/{api/sseToken.ts,hooks/streamController.ts,hooks/useTodayStream.ts,hooks/__tests__/streamController.test.ts}`, `pages/Today/TodayPage.tsx` | ✅ В `feature/topic-scoped-checkin` (`5d8c6e6` + `d30832a`). 11/11 vitest pass, tsc/eslint/build clean. На main не задеплоен. |
| Тесты Steps 1-4 локально проходят? | `apps/backend/tests/`, `apps/worker/tests/`, `apps/frontend/src/shared/hooks/__tests__/` | ✅ ДА — 67 backend + 22 worker + 11 frontend (см. ниже). Lint чистый. |

**Тесты (snapshot 2026-08-04 после Step 6):** 67 passed в backend'е
(test_sse_connection_limiter × 9 + test_sse_token × 8 +
test_events_token_api × 6 + test_sse_formatter × 7 +
test_sse_redis_stream_bus × 14 + test_sse_stream_api × **23** — было 16,
+7 на новые сценарии Step 4 + 2 anti-regression singleton-теста) + 22
новых в worker'е (test_event_publisher × 7 на fakeredis — Guard 2
идемпотентность + оба фейла Redis; test_process_checkin +5 на Guard 1,
happy path publish, rejected path, no-publisher regression, оба Redis-failure
survive-теста) + **11 новых в frontend** (test_stream_controller — vitest
2.1.9, EventSource + requestToken + queryClient моканы, `vi.useFakeTimers`
для backoff-планировщика: initial open URL shape, checkin.accepted →
setQueryData с payload + lastEventId persistence, checkin.rejected →
onError с message, onerror → close + backoff + новый EventSource с
свежим токеном, backoff cap на 10s, lastEventId в reconnect URL,
stop() отменяет pending backoff, requestToken throws → backoff retry,
start() идемпотентность).
Lint чистый (ruff check + format applied для backend/worker;
eslint + tsc --noEmit для frontend). Push выполнен в
`origin/feature/topic-scoped-checkin` (`c5bb8c5..d30832a`).

## 0. Контекст и ограничения

- **Задача:** пока юзер сидит в Mini App, бот принял чек-ин → `TodayPage` должна
  сама узнать об этом и перерисовать статус (без ручного `refetch`, без polling
  с секундным интервалом).
- **Текущее поведение:** `useToday` имеет `staleTime: 30_000` + `invalidateQueries`
  на mount (`TodayPage.tsx:74-78`). Если юзер уже на странице — кэш не
  инвалидируется до истечения 30 с.
- **Решено заранее:** SSE, не polling. Redis Streams, не Pub/Sub (важно — события
  не должны теряться при реконнекте клиента).
- **Документ не меняет ничего.** Это план для ревью. Никакого кода.

---

## 1. Разведка текущего кода (что есть, чего нет)

### 1.1 Frontend

**`useToday`** — `apps/frontend/src/shared/hooks/index.ts:19-26`

```ts
export function useToday(habitId: string | undefined) {
  return useQuery({
    queryKey: ["today", habitId],
    queryFn: () => habitsApi.today(habitId!),
    enabled: Boolean(habitId),
    staleTime: 30_000,
  });
}
```

**`TodayPage.tsx`** — **актуальное состояние** в `feature/topic-scoped-checkin` (`d30832a`):
`useToday(habitId)` через обычный `useQuery` со `staleTime: 30_000`, **без** mount-invalidate
эффекта. SSE-stale через `useTodayStream(habitId)` (см. §2.4). Mount-invalidate был
удалён как избыточный — на масштабе 1000+ юзеров это конкретная лишняя нагрузка
на backend без реальной пользы (React Query сам управляет stale-инвалидацией).

> **Исторически:** до Step 6 на строке 68-78 был такой блок:
>
> ```tsx
> useEffect(() => {
>   if (habitId) {
>     queryClient.invalidateQueries({ queryKey: ["today", habitId] });
>   }
> }, [habitId, queryClient]);
> ```
>
> Удалён в `d30832a` («fix(frontend): drop redundant mount-invalidate, SSE covers freshness»).
> React Query сам делает refetch по `staleTime`, manual форс-инвалидейт на каждый mount
> был лишним.

**API клиент** — `apps/frontend/src/shared/api/client.ts:1-25`. `axios.create()`,
база `/api/v1`, initData через interceptor (`X-Telegram-Init-Data`). **Важно
для SSE:** `EventSource` в браузере **не поддерживает кастомные заголовки**.
Это критический момент для дизайна (см. §3.2).

**`habitsApi.today()`** — `apps/frontend/src/shared/api/index.ts:18-20`:

```ts
today: (habitId: string) =>
  apiClient.get<TodayResponse>(`/habits/${habitId}/today`).then((r) => r.data),
```

### 1.2 Backend

**Sync-Redis-клиент backend** — `apps/backend/app/db/redis.py:1-33`. Синхронный
`redis.Redis` (`from_url`, `decode_responses=True`). URL берётся из
`settings.redis_url` (env `REDIS_URL`, default `redis://redis:6379/0`).
**Singleton** через `_redis` global + `get_redis()`.

**Async-Redis-клиент backend** — `apps/backend/app/db/redis_async.py` (Step 4
post-review fix). `redis.asyncio.Redis`, симметричная структура singleton'а:
`get_async_redis()` (lazy-init через `_async_redis` global) + `close_async_redis()`.
Создаётся/закрывается в lifespan `_lifespan` (`app/main.py:69-101`),
прогревается через `ping()` при старте. Используется в
`apps/backend/app/services/sse/redis_stream_bus.py` для `XREAD BLOCK` —
одиночный пул на процесс (а не `from_url()` per SSE-открытие, что
вызывало FD-leak при reconnect-loop; подробности §2.7).

**SQLAlchemy session** — `apps/backend/app/db/session.py:19-75`. Async
`async_sessionmaker`, `expire_on_commit=False`, `autoflush=False`.

**Middleware initData** — `apps/backend/app/core/middleware.py:53-173`. На
путях `/api/v1/*` берёт `X-Telegram-Init-Data`, валидирует HMAC, кладёт
`request.state.telegram_user`. **Для SSE это проблема:** см. §3.2 и §2.2
(exact-path исключение).

**Endpoint `GET /habits/{habit_id}/today`** — `apps/backend/app/api/v1/habits.py:88-127`.
Возвращает `TodayResponse` с `HabitOut + MembershipOut + CheckinStatusOut`
(`status: "done" | "missed" | "pending" | "not_started"`). Внутри
вызывает `service.get_today_status(user_id, habit_id, ...)` — сервисный
слой проверяет membership через `MembershipRepository(session)`. **Этот же
паттерн переиспользуется в token-эндпоинте** (см. §2.2).

**DI для `CheckinService`** — `apps/backend/app/api/v1/habits.py:38-49`. Уже
передаёт `RedisTodayCache` (опционально). Кэш живёт на ключе
`today:{habit_id}:{membership_id}` (см. `RedisTodayCache`).

**`RedisTodayCache`** — `apps/backend/app/services/today_cache.py`. Используется
**только** для чтения/инвалидации per-day кэша, **никакого pub/sub и Streams**.

**Где `session.commit()` для чек-ина:** сервис `CheckinService` его **не
делает** (правило layered architecture). Commit делает **worker task**
`apps/worker/worker/tasks/process_checkin.py:79` — `await session.commit()`
после `await service.process_checkin(...)`. Это и есть точка, в которой
мы знаем: чек-ин зафиксирован, можно публиковать событие.

**SSE-инфра в backend:** отсутствует полностью. `StreamingResponse` есть в
FastAPI 0.115, отдельных SSE-библиотек в `requirements.txt` нет.

**Settings** — `apps/backend/app/core/config.py`. Сейчас содержит
`service_secret: SecretStr` (для internal-контура). **Нужно добавить**
отдельный `sse_token_secret: SecretStr` (см. §2.2 и §5.Q1).

**Доменные исключения** — `apps/backend/app/core/exceptions.py`. Уже есть
`MembershipNotActiveError` (используется в `habits.py` при отсутствии
member'а в `today`-эндпоинте). **Переиспользуется** в token-эндпоинте
для 403 при выдаче токена не-члену клуба (см. §2.2).

### 1.3 Worker

**`process_checkin.py:30-119`** — async-функция `_process()`. Ключевые строки:

- `service.process_checkin(...)` — основная работа
- `await session.commit()` — строка 79
- Возвращает dict c `ok`, `reason`, `streak_days`, и т.д.
- В payload уже есть `membership_id` и `date` (нужны сервису для создания
  `Checkin` row) — это источник для idempotency-ключа, см. §2.3.

**`process_checkin.py:128-140`** — `_build_production_cache()` создаёт
**свой собственный** `redis.asyncio` клиент (`redis_url = os.getenv("REDIS_URL")`).
То есть worker уже умеет писать в Redis async — `event_publisher.py` будет
использовать тот же клиент.

**Celery конфиг** — `apps/worker/worker/celery_app.py:34-59`. Брокер —
`redis://redis:6379/1`, бэкенд результатов — `redis://redis:6379/2`. Оба
контейнера (backend, worker) делят один Redis (DB 0 для кэша и стримов,
DB 1 для broker, DB 2 для results). Разделение по namespace ключа, не по DB.

**Worker `--pool=solo`** — один процесс, async внутри. Несколько worker'ов не
гонят события параллельно, но это и не нужно: XADD атомарен.

**`process_penalty.py`** — **НЕ трогаем** в этой итерации (см. §2.5).

### 1.4 Infra

**docker-compose** — `infra/docker-compose.yml:3-8, 32-42`. Backend и worker
получают `REDIS_URL` из env. Контейнеры на bridge-сети `habit-club_default`,
DNS по именам → backend и worker общаются через `redis:6379`. `SSE_TOKEN_SECRET`
— backend-only env (см. §2.2), в `/app/infra/.env` **не нужен**, только в
`/app/.env`.

**Host nginx** — конфиг для доменов в `/etc/nginx/sites-enabled/habit-club`
(на хосте, не в репо). **Не настроен** под SSE — нет `proxy_buffering off`,
нет `access_log off` для SSE-пути (см. §3.1, §3.2).

**Что есть → что нужно создать:**

| Уже есть | Создать с нуля |
|---|---|
| Redis-клиент (sync в backend, async в worker) | ✅ Redis-Streams продюсер в worker (`event_publisher.py`) — **Step 3** |
| `RedisTodayCache` для ключа `today:{habit_id}:{membership_id}` | ✅ Redis-Streams консьюмер в backend (`redis_stream_bus.py`) — **Step 4** (`7ada2ad`) |
| `axios` клиент с initData interceptor | ⏳ SSE-клиент на фронте (обёртка над `EventSource`) — **Step 6** |
| `validate_init_data` (HMAC) + `service_token` JWT | ✅ Endpoint выдачи короткоживущего SSE-токена (свой secret) — **Step 1** |
| `Settings.service_secret` | ✅ `Settings.sse_token_secret` (отдельный) — **Step 1** |
| `MembershipRepository` + `MembershipNotActiveError` | ✅ Переиспользуются в token-эндпоинте для early 403 — **Step 1** |
| `X-Accel-Buffering` / `proxy_buffering` / `access_log off` в nginx — **нет** | ⏳ Nginx-сниппет для пути `/api/v1/events/stream` — **Step 5** |
| Celery task `process_checkin` | ✅ Ничего нового не нужно — пишем в стрим прямо из существующего task после commit (с idempotency-guard) — **Step 3** |

---

## 2. Предлагаемый подход

### 2.1 Новые файлы (backend/worker/frontend/infra)

| Путь | Зачем (одной строкой) | Статус |
|---|---|---|
| `apps/backend/app/services/sse/sse_token.py` | Генерация и валидация короткоживущего (TTL ≤ 60 с) SSE-токена, подписан **отдельным** `SSE_TOKEN_SECRET` (не `SERVICE_SECRET`) | ✅ done (Step 1) |
| `apps/backend/app/api/v1/events.py` | Два эндпоинта: `POST /events/stream/token` (выдаёт токен по initData + membership-check) и `GET /events/stream` (SSE, токен в query, см. §3.2) | ✅ done (Step 1+2) |
| `apps/backend/app/services/sse/connection_limiter.py` | Per-user concurrency limit через Lua-atomic check-and-incr (защита от DoS через replayable token, см. §2.6) | ✅ done (fix-up 2) |
| `apps/backend/app/services/sse/redis_stream_bus.py` | Абстракция продюсера/консьюмера Redis Streams (XADD / XREAD BLOCK) | ✅ done (Step 4, `7ada2ad`) |
| `apps/backend/app/services/sse/sse_formatter.py` | Сериализация событий в SSE-формат (`id`, `event`, `data`, `\n\n`) + `: heartbeat` комментарии | ✅ done (Step 4, `7ada2ad`) |
| `apps/backend/app/db/redis_async.py` | Async-Redis singleton (компаньон для sync `db/redis.py`). Lazy-init через module-level global, закрытие в lifespan shutdown | ✅ done (Step 4 post-review fix, `7ada2ad`) |
| `apps/worker/worker/services/event_publisher.py` | Async XADD-обёртка + idempotency-guard через `SET NX EX` (см. §2.3) | ✅ done (Step 3, `11edb14`) |
| `apps/frontend/src/shared/hooks/useTodayStream.ts` | React-хук с `enabled: Boolean(habitId)`, **ручной** reconnect-loop (нативный EventSource auto-reconnect не используется — см. §3.13), backoff 1→2→5→10s | ✅ done (Step 6, `5d8c6e6`) |
| `apps/frontend/src/shared/hooks/streamController.ts` | Pure-function controller (DI через 7 параметров: `habitId`, `queryClient`, `createEventSource`, `requestToken`, `setTimeoutFn`, `clearTimeoutFn`, `onError`, `streamBaseUrl`). Тестируется без React-renderer — отсюда отдельный `__tests__/streamController.test.ts` с 11 vitest unit | ✅ done (Step 6, `5d8c6e6`) — добавлено **сверх** исходного плана: выделение controller'а упростило тестирование без `@testing-library/react` (его нет в `package.json`) |
| `apps/frontend/src/shared/api/sseToken.ts` | Запрос короткоживущего токена через обычный axios (с initData) перед открытием EventSource | ✅ done (Step 6, `5d8c6e6`) |
| `infra/nginx/habit-club-sse.conf.snippet` | Сниппет с `proxy_buffering off`, `proxy_read_timeout 3600s`, `access_log off` для пути `/api/v1/events/stream` (см. §3.1, §3.2) | ✅ done (Step 5, `900ef4f` + ручное применение) |

### 2.2 Изменения в существующих файлах

**`apps/backend/app/core/config.py`** — ✅ done (Step 1, `c836542`):
добавить поле (ниже — фактически используем `str` для консистентности с `service_secret`):

```
sse_token_secret: SecretStr
```

Читается из env `SSE_TOKEN_SECRET`. **Не** дефолтится, **не** шарится с
`SERVICE_SECRET`. Причина — принцип «разные URL — разные уровни доверия»
(см. `AGENTS.md`): `/internal/*` = service-token для backend↔bot↔worker,
`/api/v1/events/*` = пользовательский SSE-контур. Компрометация одного
секрета не должна давать доступ к другому контуру.

**`apps/backend/app/core/middleware.py`** — ✅ done (Step 2 `9d5b374` +
fix-up 1 `a0217ec`): exact-path исключение для SSE.

**Зафиксировано:** исключение **строго** через
`if path in SSE_AUTH_BYPASS_PATHS:` где
`SSE_AUTH_BYPASS_PATHS = {"/api/v1/events/stream"}` (точный set, не
префикс). Такой же bypass в `RateLimitMiddleware`. DoS через
replayable токен решается per-user concurrency лимитом (см. §2.6),
не общим rate-limit'ом.

Если в будущем появится `GET /api/v1/events/history` или подобное — оно
**не** попадёт под bypass (точный set, не префикс), останется под
initData-проверкой. Тест `test_similar_path_under_events_is_not_bypassed`
это проверяет.

Размещение bypass в `AuthMiddleware.dispatch` — до проверки
`X-Telegram-Init-Data` для текущего `PUBLIC_PREFIX` блока:

**`apps/backend/app/api/v1/events.py`** (новый) — ✅ done (Step 1+2).
Два роута под `/api/v1/events/`:

**`POST /events/stream/token`** (требует initData через interceptor) —
тело запроса: `{habit_id: str}`. Логика:

1. Достать `user.id` из `request.state.telegram_user` (middleware уже
   валидировал initData для этого пути — он под `PUBLIC_PREFIX`).
2. Через DI-цепочку (паттерн из `habits.py`) — `MembershipRepository(session)`,
   `get_for_user_in_habit(user_id, habit_id)`. Если не найден или не `ACTIVE` —
   `SseStreamForbiddenError(status_code=403, code="membership_not_active")`
   (новое исключение, `core/exceptions.py`). **Использовали 403, не 400**
   (как у `MembershipNotActiveError`) — это "не имеешь права", не "плохой запрос".
3. Если member active — `generate_sse_token(...)` → JWT с claims
   `sub=user_id, habit_id=habit_id, scope="sse:today", aud="sse-stream",
   iss="backend", iat, exp=now+60s`. Подписан `SSE_TOKEN_SECRET` (HS256).
   Leeway=10с на валидации — устойчивость к дрейфу часов + reconnect-флоу.
4. 503 `sse_not_configured` если `SSE_TOKEN_SECRET` пуст —
   ops-проблема, не баг юзера, не делаем retry.

**Зачем membership-check на этапе выдачи токена, а не на стриме:**
- Ранний fail-fast — клиент сразу видит 403, не открывает EventSource
  зря.
- Логи в дебаге чище — если фронт жалуется "SSE не работает", первое
  подозрение это не membership, а сеть/proxy/токен.
- Устраняет паразитный трафик: пустой стрим с XREAD в течение 30 с
  блокирует воркер и соединение ради нуля событий.

**`GET /events/stream?habit_id=…&token=…&last_event_id=…`** (НЕ требует
initData — exact-path исключение в middleware, см. выше). Логика:

1. Достать из query: `habit_id` (обязателен), `token` (обязателен),
   `last_event_id` (опционален — **зафиксирован в контракте с Step 2**,
   чтобы Step 4 не менял сигнатуру).
2. `validate_sse_token(token, secret, expected_habit_id=habit_id)` —
   проверка подписи, `exp` (с leeway=10с), `aud=sse-stream`,
   `scope=sse:today`, `habit_id` claim == expected. При невалидном — 401.
3. **Не** делать повторный membership-check (он уже был при выдаче токена,
   +60 с — membership не мог измениться так быстро, +1 RTT к БД
   неоправдан). Принцип: токен = delegated authorization на 60 с.
4. Сверить `habit_id` из query с claim — отказ если не совпадают.
5. `SseConnectionLimiter(get_redis()).try_acquire(user_id)` — per-user
   concurrency limit (см. §2.6). При исчерпании — 429 `too_many_sse_connections`.
6. `request.is_disconnected()` check в генераторе + `finally`-блок
   с `connection_limiter.release(user_id)` — покрывает оба пути выхода
   (CancelledError от uvicorn shutdown и обычное закрытие EventSource).
7. ✅ **Step 4 реализован (`7ada2ad`):** `StreamingResponse(media_type="text/event-stream")` с
   `_sse_event_stream_generator` — реальный `XREAD BLOCK 30000 STREAMS sse:user:{u}:{h} <start_id>`
   через `RedisStreamBus.read_blocking`. `<start_id>` резолвится в эндпоинте
   из приоритета `Last-Event-ID` header > `last_event_id` query > `$`. На каждый
   event — формат `id: <stream-id>\nevent: <event-name>\ndata: <payload-json>\n\n`
   через `sse_formatter.format_event_frame`. На пустой XREAD — `: heartbeat\n\n`
   (SSE-комментарий, держит proxy живым). Async-Redis-клиент — process-level
   singleton из `db/redis_async.py` (см. §2.7).

**`apps/worker/worker/tasks/process_checkin.py`** — ✅ **done** (Step 3, `11edb14`)
+ пост-ревью фикс `e5cc8e0` (SET NX + XADD под единым try/except).

Реализация (с отклонениями от первоначального плана, отмеченными ниже):

1. **Guard 1 (early-skip):** фактический триггер — `result["duplicate"] is True`
   в result-дикте `_process()`, **а не** `result["reason"] == "already_exists"`
   как было в плане. Это потому что `_process` возвращает
   `{ok: True, duplicate: True}` в **двух** ветках исключений:
   `CheckinAlreadyExistsError` и `IntegrityError` (race на уникальный
   индекс `(membership_id, date)`), а также в success-пути при
   `created=False`. Все три источника означают "в БД уже есть Checkin за
   сегодня, UI уже показывает done" → Guard 1 срабатывает до Redis-операций.
2. **Guard 2 (idempotency):** реализован внутри `EventPublisher.publish_checkin`
   (сервис-слой, не в самой таске). Ключ `sse_published:checkin:{m}:{d}`,
   `SET key "1" NX EX 86400`.
3. Если SET вернул `None` (ключ уже есть; redis-py 5.x возвращает `None`,
   не `False` — учитываем через `if not acquired:`) → повторная доставка →
   `return False`, XADD не выполняется.
4. Если SET вернул `True` → XADD выполнен с `MAXLEN ~ 1000`, возвращён `True`.
5. **Единый try/except** (пост-ревью фикс `e5cc8e0`): SET NX и XADD под
   одним блоком защиты. Redis outage на **любой** стадии логируется как
   `sse_publish_failed` warning, возвращается `False`. Чек-ин уже в БД —
   publish не должен ломать task. Семантика at-most-once для обеих стадий:
   - SET NX упал → ключа нет. При Celery retry Guard 1 в `_process` сработает
     через `CheckinAlreadyExistsError` → `duplicate=True` → skip публикации.
   - XADD упал → ключ УЖЕ есть. Повторная доставка Guard 2 skip'нет XADD.
6. **Payload `checkin.accepted`:** полный `TodayResponse` (как у
   `GET /habits/{id}/today`). Сборка в `_build_today_payload()` через
   **отдельную** DB-сессию (основная уже release'нута после `async with`).
   Переиспользует `HabitOut`/`MembershipOut`/`CheckinStatusOut`/`TodayResponse`
   из `app/schemas/__init__.py`. Явные `await self._*_repo.*()` запросы —
   нет lazy-load через async-сессию (relationship-полей в схемах нет,
   подтверждено сквозным интеграционным тестом
   `test_process_checkin_happy_path_publishes_accepted`).
7. **Payload `checkin.rejected`:** `{habit_id, reason, message}`. `reason`
   берётся из `exc.code` (доменные исключения `CheckinWindowClosedError`,
   `ProofValidationError`, `CheckinWrongTopicError`, `MembershipNotActiveError`).
   `MembershipNotFoundError` пропускает публикацию (нет membership_id для
   идемпотентности, и в UI рисовать нечего).
8. **DI через конструктор:** `_process(payload, *, cache, publisher,
   session_factory)` — все три параметра опциональны для тестов.
   Прод-обёртка `run` создаёт `_build_production_cache()` +
   `_build_production_publisher()` (lazy import `redis.asyncio.from_url`,
   тот же `REDIS_URL`).
9. **Тесты:** `tests/test_event_publisher.py` (7 unit на fakeredis —
   Guard 2 skip, разные date_iso → разные события, rejected payload,
   XADD-фейл → False с ключом, SET-фейл → False без ключа, формат ключей);
   `tests/test_process_checkin.py` +5 (Guard 1 skip, happy path publish
   с правильным TodayResponse, rejected path publish, regression
   publisher=None, MembershipNotFound skip, плюс 2 интеграционных
   на Redis outage: `_process` не падает, чек-ин в БД остаётся).

**`apps/backend/app/main.py`** — ✅ done (Step 1, `c836542`): зарегистрирован
`events.router` с префиксом `/api/v1`.

**`apps/frontend/src/pages/Today/TodayPage.tsx`** — ✅ **done** (Step 6 `5d8c6e6` + фикс `d30832a`).
Подключён `useTodayStream(habitId)`. **Mount-invalidate `useEffect(invalidateQueries)` удалён** (`d30832a`):
useToday через обычный `useQuery` со `staleTime: 30_000` уже даёт свежий state при
следующем mount, лишний форс-инвалидейт на каждом заходе на страницу — конкретная
нагрузка на backend без реальной пользы (на масштабе 1000+ юзеров — заметная).
SSE даёт real-time freshness через `setQueryData`, React Query сам управляет
stale-инвалидацией. Никаких race-conditions и двойной работы.

**`apps/backend/app/db/redis.py`** — ✅ остаётся sync-singleton без изменений.
SSE-консьюмер использует **отдельный** async-singleton из
`apps/backend/app/db/redis_async.py` (Step 4 post-review fix, см. §2.7).
Изначально Step 4 задумывал `redis.asyncio.from_url()` per request,
но ревью поймало FD-leak — singleton на процесс вместо этого.

**`apps/worker/worker/tasks/process_penalty.py`** — **НЕ трогаем** в этой
итерации (см. §2.5 — `penalty.applied` в v2, не в MVP).

**`infra/docker-compose.yml`** — **не меняется.** Worker и backend уже
имеют `REDIS_URL`. `SSE_TOKEN_SECRET` — backend-only env, читается из
`/app/.env`, в compose не пробрасывается.

**`.env.example`** — ✅ done (Step 1): добавлены `SSE_TOKEN_SECRET` и
`SSE_TOKEN_TTL_SECONDS`.

**`/app/.env` (прод)** — ⏳ **Step 8 (деплой)**: сгенерировать
`SSE_TOKEN_SECRET` через
`python -c "import secrets; print(secrets.token_urlsafe(48))"` и добавить.
**Не** в `/app/infra/.env`.

**`/etc/nginx/sites-enabled/habit-club`** (на хосте, не в репо) — внутри
`location /api/v1/events/stream` (точно этот путь, не префикс, не
включая `POST /events/stream/token`):

```
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
proxy_set_header Connection '';
add_header X-Accel-Buffering no;
access_log off;     # SSE-токен живёт в query 60с, лог-утечки initData-стиля
```

`POST /events/stream/token` **не** попадает под `access_log off` — это
редкий вызов (один раз при монтировании страницы), audit-лог полезен,
токен там не светится (см. §3.2).

### 2.3 Структура Redis-ключей

**Имя стрима:** `sse:user:{user_id}:{habit_id}` (тип: Redis Stream).

**Почему per-(user, habit):**
- `useToday` уже параметризован `habitId` → естественная гранулярность.
- Один пользователь с несколькими habits в нескольких вкладках = несколько
  независимых стримов, нет необходимости фильтровать на стороне сервера.
- Альтернатива `sse:user:{user_id}` (один стрим на юзера, события с `habit_id`)
  усложняет серверный фильтр и создаёт race с подписками на несколько habits.

**Структура entry (XADD):**

```
*  →  fields:
  event:        "checkin.accepted" | "checkin.rejected"
  habit_id:     "uuid"
  user_id:      "12345"          (numeric, не PII)
  occurred_at:  "2026-08-04T16:00:00Z"  (ISO 8601 UTC)
  payload:      "{json}"          (status, streak_days, checkin_count, penalties_total — НЕ PII)
```

**Retention:** `XADD sse:user:... MAXLEN ~ 1000` — приблизительное усечение
последних ~1000 событий (Redis Streams `MAXLEN ~` — O(1), не блокирует).
1000 событий хватает на 24+ часов лага при любом разумном темпе.
TTL/cleanup явный не нужен — `MAXLEN` достаточно.

**TTL SSE-токена:** 60 секунд. Claims: `sub=user_id, habit_id=habit_id,
scope="sse:today", exp=now+60s`. Подписан `SSE_TOKEN_SECRET` (отдельный
HS256-секрет, не `SERVICE_SECRET`).

**Идемпотентность publish:**

| Ключ | Тип | TTL | Назначение |
|---|---|---|---|
| `sse_published:checkin:{membership_id}:{date}` | string `"1"` | 86400 с (24 ч) | Защита от двойной публикации при Celery retry/rebalance |

**Механика:**
1. Worker вычисляет `membership_id` и `date` из входного `payload`
   (поля уже там — `CheckinService.process_checkin` использует их для
   создания `Checkin` row).
2. Перед XADD выполняет `await redis.set(key, "1", nx=True, ex=86400)`.
3. Если вернулось `False` — повторная доставка → XADD не делать, return.
4. Если вернулось `True` — выполнить XADD, событие опубликовано.

**TTL 24 ч:** покрывает полный день (с учётом таймзон club'ов, ±14 ч) плюс
окно Celery retry (макс 60 с × 3 попытки + backoff). После 24 ч ключ
expire'ится, но повторная публикация того же `(membership_id, date)` за
пределами суток не имеет смысла.

**Special case `already_exists`:** если `service.process_checkin()` вернул
`reason == "already_exists"` (CheckinAlreadyExistsError или аналог — дубль
в уникальном индексе `(membership_id, date)`), Guard 1 (early-skip) **до
SET NX** — никаких Redis-операций вообще. Это либо повторная доставка
того же task'а после успешного commit первой попытки, либо пользователь
отправил два видео-кружка подряд (что запрещено UI, но возможно при race).
В обоих случаях UI уже показывает `status=done`, событие бесполезно.

**Trade-off:** семантика «at-most-once» — если XADD упал после успешного
SET NX, событие потеряно. При повторной Celery-доставке Guard 1 сработает
на `already_exists` (Checkin row уже в БД) и событие не опубликуется.
**Приемлемо для MVP** — SSE-событие это UI-hint, не финансовая операция.

### 2.4 Обработка Last-Event-ID и реконнект

**Browser EventSource** автоматически шлёт `Last-Event-ID: <id>` при реконнекте
(где `<id>` — это то, что сервер прислал в поле `id:` последнего принятого события).
Redis Streams генерирует монотонный ID вида `<ms-timestamp>-<seq>`, который
можно прямо использовать как SSE `id` (без трансформации).

**Server-side flow на `GET /events/stream`:**

1. Валидировать `token` из query → `user_id`, `habit_id` из claims.
2. Сверить `habit_id` из query с claim — отказ если не совпадают.
3. Проверить `request` на закрытие (EventSource-disconnect) через
   `request.is_disconnected()` (FastAPI) или генератор-`try/finally`.
4. В цикле:
   - Если есть `Last-Event-ID` header **или** `last_event_id` из query →
     используем его как начальный ID для `XREAD BLOCK 30000 COUNT 100 STREAMS sse:user:{u}:{h} <id>`.
     Нативный EventSource шлёт `Last-Event-ID` только в рамках одной инстанции
     (при её жизни); для ручного reconnect'а с новым токеном фронт
     передаёт `last_event_id` через query (см. §2.4 Frontend flow).
   - Если нет ни того, ни другого → `XREAD BLOCK 30000 COUNT 1 STREAMS sse:user:{u}:{h} $`.
     `$` = "только новые", не воспроизводит старые события.
   - Для каждого entry: распарсить поля → отформатировать SSE
     (`id: <stream-id>\nevent: <event-name>\ndata: <json>\n\n`).
   - Если результат пустой (таймаут 30 с) — отправить `: heartbeat\n\n`
     SSE-комментарий (не считается событием, держит соединение живым и
     прокси будит).
   - При `request.is_disconnected()` — `break` → генератор завершается → XREAD
     отменяется → соединение закрывается чисто.

5. На повторном коннекте с устаревшим `Last-Event-ID` или `last_event_id`
   (старше retention): сервер попытается XREAD с этим ID, получит пустой
   результат (событие уже trimmed — нормализуется к next-available Redis
   semantics). Генератор продолжает ждать новых событий с тем же start_id.
   **Событие потеряно, но клиент не зависнет** (Step 4, реализовано —
   см. также §3.10). Фронт может дополнительно дёрнуть `refetch()` после
   коннекта как safety-net.

**Frontend flow (`useTodayStream` + `streamController`) — реализация Step 6:**

> **Изменение от исходного плана:** в плане предполагался inline-хук
> (см. псевдокод ниже). При реализации (`5d8c6e6`) **выделен чистый
> controller `createStreamController`** с constructor-style DI —
> тестируется без `@testing-library/react` (его нет в `package.json`),
> 11 vitest unit покрывают reconnect-логику, lastEventId resume,
> backoff cap, idempotent start(), stop() отмену pending таймера и т.д.

**Архитектура:**

1. **`streamController.ts`** — pure function с DI через 7 параметров:
   - `habitId: string`
   - `queryClient: QueryClient` (полный тип из `@tanstack/react-query`,
     не подмножество — DI-граница на чистом интерфейсе не нужна,
     `QueryClient.setQueryData` structural-compatibel с подмножеством)
   - `createEventSource: StreamEventSourceCtor` (DI: в проде `EventSource`,
     в тестах `MockEventSource`)
   - `requestToken: (habitId) => Promise<{ token, expires_at }>` (DI:
     в проде `sseTokenApi.request`, в тестах `vi.fn()`)
   - `setTimeoutFn?: typeof setTimeout`, `clearTimeoutFn?: typeof clearTimeout`
     (DI: в проде дефолты, в тестах моканы через `vi.useFakeTimers`)
   - `onError?: (message: string) => void` (DI: в проде дефолт
     `Telegram.WebApp.showAlert → console.warn fallback`, в тестах `vi.fn()`)
   - `streamBaseUrl?: string` (default `/api/v1`)

   Возвращает `{ start(), stop(), state: { isStarted, lastEventId, attempt } }`.

2. **`useTodayStream.ts`** — тонкая обёртка (~45 строк) с `useEffect`+`useRef`:
   - Зеркалит `useToday(habitId)` по семантике `enabled: Boolean(habitId)` —
     при `habitId === undefined` хук не делает ничего, никакого EventSource,
     никакого токен-запроса.
   - На mount с валидным `habitId` создаёт controller, вызывает `start()`,
     в cleanup — `stop()` (закрывает EventSource, отменяет pending backoff-таймер).
   - **Не полагается на нативный auto-reconnect EventSource** — обоснование
     в §3.13 (TTL токена 60с, нативный реконнект ре-шлёт протухший токен,
     EventSource при 401 закрывается насовсем → "SSE иногда работает, иногда нет").

**Reconnect-цикл внутри `createStreamController`:**

```
start() → open():
  1. requestToken(habitId)         ← axios через apiClient (initData rides on interceptor)
  2. URL = `/api/v1/events/stream?habit_id=…&token=…&last_event_id=<tracked>`
  3. es = new EventSource(URL)
  4. bind listeners:
     - checkin.accepted → JSON.parse(data) → queryClient.setQueryData(["today", habitId], today)
     - checkin.rejected → JSON.parse(data).message → onError(message)
     - onerror → es.close() + scheduleReconnect()
  5. scheduleReconnect():
     - delay = BACKOFFS_MS[min(attempt, 3)]   ← [1000, 2000, 5000, 10000]
     - attempt += 1
     - setTimeout(open, delay)
     - сбрасывается на 0 при успешном checkin.accepted
  6. stop() → clearTimeout + es.close() — отменяет pending backoff
```

**Защита от race-conditions:**
- `inFlight` флаг — если backoff-таймер сработал во время ещё не завершившегося
  `requestToken`, повторный `open()` skip-ится (без него были бы два EventSource
  на одно соединение).
- `attempt` НЕ сбрасывается на onerror, только на успешный checkin.accepted —
  это правильно: на onerror мы ничего не знаем о реальной проблеме, бэкенд
  может быть жив, просто сеть моргнула.

**One `as unknown as` каст в одной DI-точке:**
`EventSource as unknown as StreamEventSourceCtor` — TypeScript не делает
covariance на constructor return types (`EventSource` имеет больше свойств:
readyState/url/onopen/onmessage). Каст помечен комментарием с обоснованием,
`any` не используется.

**Изначальный псевдокод из плана (для справки):**

```ts
export function useTodayStream(habitId: string | undefined) {
  useEffect(() => {
    if (!habitId) return;
    let es: EventSource | null = null;
    let cancelled = false;
    let lastEventId: string | null = null;
    let reconnectAttempt = 0;
    const BACKOFFS_MS = [1000, 2000, 5000, 10000];

    const connect = async () => { /* … как в Step 3-плане … */ };
    connect();
    return () => { cancelled = true; es?.close(); };
  }, [habitId, queryClient]);
}
```

**Не реализован** в финальной версии — заменён на `createStreamController`
(см. выше). Изначальный псевдокод не тестируется напрямую без
`@testing-library/react`.

### 2.6 Per-user SSE concurrency limit (защита от DoS)

> **Добавлено после ревью Step 2** (`ec60c0f`). Не было в исходном плане —
> обнаружено при обсуждении `RateLimitMiddleware` bypass: токен не одноразовый
> (TTL 60с, осознанное решение Q4), значит один валидный токен открывает
> неограниченное число соединений в течение 60с — DoS-вектор на FD.

**Файл:** `apps/backend/app/services/sse/connection_limiter.py` ✅ done.

**Семантика:** per-user счётчик в Redis DB 0, атомарный Lua-скрипт:

```lua
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if count > tonumber(ARGV[1]) then
    redis.call('DECR', KEYS[1])
    return -1
end
return count
```

`INCR + EXPIRE на ПЕРВОМ + проверка + DECR-rollback` в одном atomic-блоке.
Шаблон взят из `RedisCatchRateLimiter` (`catch_rate_limiter.py`).

**Константы** (с обоснованием в docstring):
- `MAX_CONCURRENT_CONNECTIONS_PER_USER = 5` — типичный юзер держит 1 EventSource
  на активной вкладке. У активного — 3-4 клуба одновременно (по одному
  стриму на habit, ключ `sse:user:{u}:{habit_id}`) + дубль от reconnect-race
  на фронте (предыдущий EventSource ещё не закрылся, новый уже открывается).
  5 = небольшой запас над типичным, не безлимит.
- `CONNECTION_TTL_SECONDS = 180` — страховка от permanent leak при `kill -9`
  uvicorn worker'а. При graceful shutdown (uvicorn graceful_timeout ~30с)
  cleanup проходит через `DECR` в `finally` генератора. TTL = 3× максимального
  ожидаемого времени жизни соединения.

**Release-скрипт** — атомарный clamp-decr: если DECR уходит в -1
(TTL истёк между acquire и release) — `DEL` ключа, чтобы следующий
INCR начал с 0 и поставил TTL.

**Точки интеграции:**
1. `stream_sse_events` (после валидации токена, перед `StreamingResponse`):
   `SseConnectionLimiter(get_redis()).try_acquire(user_id)` → при `False`
   выбросить `TooManySseConnectionsError(429, code="too_many_sse_connections")`.
2. `_sse_heartbeat_generator` (finally-блок): `await connection_limiter.release(user_id)`.
   Покрывает ОБА пути выхода:
   - `request.is_disconnected() → True → break` (обычный disconnect)
   - `CancelledError` от Starlette/uvicorn (worker shutdown)
   Try/except вокруг release + warning-лог — Redis cleanup-failure не валит
   генератор; TTL=180с страхует от permanent leak.

**Тесты:**
- `tests/test_sse_connection_limiter.py` (9 unit на fakeredis): Lua-атомарность,
  TTL только на ПЕРВОМ, rollback при overshoot, release идемпотентность,
  clamp-decr при отрицательном счётчике, per-user isolation.
- `tests/test_sse_stream_api.py` (+2 теста): оба пути выхода из генератора
  (CancelledError + is_disconnected) реально освобождают слот.
- `test_too_many_sse_connections_returns_429` — интеграционный через
  TestClient с подменой `get_redis` на fakeredis.

**Не делаем:** общий rate-limit возвращать не нужно. Это **per-connection
concurrency**, не RPS.

### 2.7 Async-Redis singleton (Step 4 post-review fix)

> **Добавлено после ревью Step 4** (внутри `7ada2ad`). Не было в
> исходном плане — обнаружено при ревью: `redis.asyncio.from_url()`
> per request создаёт новый connection pool на каждое SSE-открытие,
> без явного `aclose()` пулы накапливаются с каждым reconnect в
> Telegram WebView → FD-leak.

**Файлы:** `apps/backend/app/db/redis_async.py` (new, симметричный
sync-аналогу `db/redis.py`) + правки `app/main.py` (lifespan warm/close),
`app/core/deps.py` (`AsyncRedisDep` alias), `app/api/v1/events.py`
(`get_async_redis()` вместо `from_url()`).

**Почему синглтон, а не свежий клиент на запрос:**
- `redis.asyncio.from_url()` создаёт **новый connection pool** на
  каждый вызов. SSE-соединения у клиента короткоживущие (reconnect
  на каждый сбой сети в Telegram WebView), `aclose()` на них
  естественно не вызывается → пулы накапливаются, FDs упираются в
  лимит именно того класса, против которого весь этот дизайн и
  проектировался.
- В worker'е тот же подход: `_build_production_cache()` создаёт
  клиента один раз, шарят между тасками.
- Симметрия с sync `db/redis.py` — sync-клиент уже синглтон, async
  не должен быть хуже.

**Паттерн идентичен sync-клиенту:**

```python
# apps/backend/app/db/redis_async.py
_async_redis: Redis | None = None

def get_async_redis() -> Redis:
    global _async_redis
    if _async_redis is None:
        _async_redis = from_url(
            _settings.redis_url, encoding="utf-8", decode_responses=True,
        )
    return _async_redis

async def close_async_redis() -> None:
    global _async_redis
    if _async_redis is not None:
        await _async_redis.aclose()
        _async_redis = None
```

**Lifespan (`app/main.py`):**
- Startup: `await async_redis.ping()` под try/except (degraded start).
- Shutdown: `await close_async_redis()` в finally.

**DI alias в `app/core/deps.py`:**
```python
AsyncRedisDep = Annotated[Redis, Depends(get_async_redis)]
```

**Точка интеграции в SSE-эндпоинте:**
```python
# apps/backend/app/api/v1/events.py:stream_sse_events
stream_redis = get_async_redis()
stream_bus = RedisStreamBus(stream_redis)
```

**Тесты:**
- `tests/test_sse_stream_api.py::TestAsyncRedisSingleton` (2 теста):
  1. `test_get_async_redis_is_module_level_singleton` — 100 вызовов
     `get_async_redis()` возвращают один и тот же объект; фабрика
     `from_url` вызвалась ровно 1 раз (через monkeypatch-счётчик).
  2. `test_singleton_reused_across_n_endpoint_opens` — 50
     "виртуальных endpoint-открытий" → `from_url` всё равно 1 раз.
- **Anti-regression свойство:** если регрессия вернёт
  `from_url()` per request в коде эндпоинта, счётчик станет 50/100,
  тесты упадут. Проверено вручную: broken-паттерн → 50 distinct
  FakeRedis, fixed → 1 shared.
- Существующие тесты Step 2 (`test_too_many_sse_connections_returns_429`)
  адаптированы — `monkeypatch.setattr` на `app.api.v1.events.get_async_redis`
  вместо `from_url`.

### 2.5 События MVP и формат payload

| `event` | Триггер | Пропуск условия | Payload (JSON) |
|---|---|---|---|
| `checkin.accepted` | `process_checkin` → `ok=True` после commit | — Guard 1: `reason == "already_exists"` → skip целиком. <br/> — Guard 2: `SET sse_published:checkin:{m}:{d} NX EX 86400` = False → skip | полный `TodayResponse` |
| `checkin.rejected` | `process_checkin` → `ok=False` после commit | — Guard 1: `reason == "already_exists"` → skip целиком (это дубль, не реальный reject). <br/> — Guard 2: `SET NX EX` = False → skip. | `{habit_id, reason, message}` (reason из существующих кодов: `window_closed`, `proof_invalid`, `too_short`, `forwarded`, и т.д.) |

**Не в MVP** (отдельная итерация, PR после): `penalty.applied`,
`catch.received`. Архитектура стрима и idempotency-ключа расширяется
тривиально (`sse_published:penalty:{m}:{d}`, `SET NX EX 86400`, новые
`event`-имена), но в этой итерации не реализуется — удерживаем размер
изменений.

**`process_penalty.py`** — намеренно **не** модифицируется в этом PR.
Штрафы применяются по cron'у `close_catch_window` и `process_penalty`,
но real-time обновление статуса члена клуба при срабатывании штрафа — это
отдельная задача (новые события, новый idempotency-key, новый UI-feedback).

**`already_exists` — отдельный путь, не событие:** реальный reject
(`window_closed`, `proof_invalid`, `too_short`, `forwarded` — из
`CheckinService.validate_proof`) **публикуется** как `checkin.rejected`
через Guard 2. `already_exists` означает "чек-ин на сегодня уже есть" —
это либо:

- Повторная Celery-доставка первой попытки (commit прошёл, task
  ребутнулся до return).
- Юзер отправил два видео-кружка подряд в бота (UI обычно блокирует, но
  возможно при race).

В обоих случаях у пользователя уже есть `Checkin` row за сегодня и
статус в UI уже `done`. Никакого UI-feedback'а не нужно — Guard 1
срабатывает до Redis-операций.

---

## 3. Риски и открытые вопросы

### 3.1 Двухслойный nginx и буферизация (HIGH)

- Host nginx на `169.58.52.78` reverse-proxy'ит 443 → `127.0.0.1:8000` (backend).
  По умолчанию nginx **буферизует** проксируемые ответы (proxy_buffering on),
  SSE-стрим будет копиться в буфер 60+ секунд, потом отдаваться одним куском.
  **Без `proxy_buffering off` ничего не работает.**
- Frontend nginx (внутри `habit-club-frontend`) тут **не участвует** — SSE
  идёт напрямую backend → `api.prideclub.fun`, минуя `app.prideclub.fun`.
- Также важно: `proxy_read_timeout` по умолчанию 60 с — nginx закроет
  соединение после минуты тишины. Нужно `proxy_read_timeout 3600s`.
- **Митигация:** добавить `proxy_buffering off` + `proxy_read_timeout 3600s`
  для `location /api/v1/events/stream`. Изменение в `/etc/nginx/sites-enabled/habit-club`
  на хосте — **вне репо**, требует ручного применения через
  `ssh privichki-prod`.

### 3.2 EventSource, initData и лог-риск (HIGH)

- Браузерный `EventSource` **не позволяет передавать кастомные заголовки**.
  Значит, `X-Telegram-Init-Data` напрямую не отправить.
- **Решение:** вариант (A) — двухступенчатый flow:
  `POST /events/stream/token` с initData → токен → `EventSource(...?token=...)`.
  Токен подписан **отдельным** `SSE_TOKEN_SECRET` (не `SERVICE_SECRET` —
  разные контуры, разные секреты, см. §5.Q1).
- **Лог-риск токена в query:** access-лог nginx по умолчанию пишет полный
  URL включая query string. **Решение:** `access_log off;` для
  `location /api/v1/events/stream` (только GET, не POST-token). POST
  остаётся под обычным логированием — это редкий вызов, audit полезен,
  токен там не светится (token только в response body).
- Альтернатива `fetch() + ReadableStream` отвергнута: нет auto-reconnect,
  ~80 строк ручной работы, никаких преимуществ.

### 3.3 uvicorn `--workers 2` (MEDIUM)

- Backend uvicorn запущен с 2 воркерами. SSE-соединение sticky к одному
  воркеру на всю жизнь соединения — это нормально (нет shared state).
- **Реконнект может попасть на другой воркер.** Это не проблема: новый
  воркер делает свежий XREAD с `Last-Event-ID` от клиента. Никакого
  shared state между воркерами не нужно.
- **Watch-out:** если воркер A закроет соединение (graceful shutdown),
  клиент реконнектится → воркер B. Если в этот момент XADD произошёл
  с id `<Last-Event-ID>`, воркер B прочитает его с `XREAD STREAMS ... > <Last-Event-ID>`.
  ОК.
- **Риск:** воркер A мог не успеть отдать событие до shutdown, клиент
  переподключился с `Last-Event-ID = последнее отправленное`, воркер B
  подхватывает с этого ID — всё ОК. **Нет потерь.**
- **Идемпотентность на retry задачи:** Guard 2 (`SET NX EX`) защищает от
  двойного XADD при Celery redelivery, см. §2.3.

### 3.4 worker `--pool=solo` (LOW)

- Один процесс, async внутри. XADD из worker — синхронный с точки зрения
  Redis, нет гонки. Не нужно думать о распределении. **Не проблема.**

### 3.5 Какие события публиковать — РЕШЕНО

- **MVP:** только `checkin.accepted` и `checkin.rejected` (см. §2.5).
- **v2 (отдельный PR):** `penalty.applied`, `catch.received`. Архитектура
  стрима и idempotency-ключа расширяется без breaking changes —
  добавляется новый namespace `sse_published:penalty:{m}:{d}` и новые
  `event`-имена.

### 3.6 Дедупликация событий — РЕШЕНО

- **Механика:** `SET sse_published:checkin:{membership_id}:{date} "1" NX EX 86400`
  перед XADD. False → skip (Celery redelivery). True → XADD. См. §2.3.
- **Special case `already_exists`:** Guard 1 (early-skip) — никаких Redis-операций.
  Это дубль чек-ина, UI уже показывает `done`, событие не нужно. См. §2.5.

### 3.7 CORS / Origin (LOW)

- CORS allowed origins = `https://web.telegram.org` only (по AGENTS.md).
  EventSource отправляет Origin автоматически. Нужно проверить, что CORS
  middleware не отбивает SSE-запрос. **Вероятно, ОК** — это GET, а не OPTIONS.
  Но стоит проверить на dev-стенде перед merge.

### 3.8 Лимиты коннектов (LOW)

- EventSource на каждую вкладку = одно соединение. Браузер обычно лимитирует
  ~6 SSE на origin. Telegram Mini App — одна вкладка. **Не проблема для MVP.**
- На сервере: 2 uvicorn workers × fd_limit. С десятками активных юзеров —
  нет проблем. С тысячами — придётся думать о backlog'е nginx (default 1024).
  Это в горизонт «когда вырастем», не сейчас.

### 3.9 Heartbeat-интервал — РЕШЕНО (30 с)

- `XREAD BLOCK 30000` — 30 секунд. Достаточно часто для nginx
  `proxy_read_timeout` (поднят до 3600s в нашем конфиге), не слишком
  часто для шума. **Подтверждено пользователем.**

### 3.10 Snapshot при reconnect со старым Last-Event-ID (LOW) — РЕШЕНО в Step 4

- **Изначальный план:** если `Last-Event-ID` старше retention (`MAXLEN ~ 1000`) —
  XREAD вернёт пустой результат, сервер переключится на `$` и потеряет события
  между trimmed ID и `$`.
- **Step 4 реализация (`7ada2ad`):** переключения на `$` не делаем — генератор
  продолжает ждать новых событий с тем же trimmed ID (Redis нормализует
  start_id за пределы стрима к next-available, XREAD вернёт next реальный
  event). Это **проще** и **безопаснее**: нет риска "откатить start_id",
  который сложно отлаживать. Клиент в этом случае теряет событие, но
  соединение остаётся живым.
- **Safety-net `invalidateQueries` после коннекта НЕ реализован** —
  удалён в `d30832a` как избыточный (см. §1.1, §2.4, финальная реализация
  Step 6). SSE-стрим сам держит кэш актуальным через `setQueryData`;
  React Query отдельно управляет `staleTime`-driven refetch при mount.
- Допустимо для MVP (это UI-hint, не финансовая операция).
- **Альтернатива (v2):** при reconnect с устаревшим ID сервер отправляет
  текущий снимок как первое событие `snapshot.today`. Удорожает дизайн.

### 3.11 Безопасность токена (LOW)

- SSE-токен подписан **отдельным** `SSE_TOKEN_SECRET` (HS256), TTL 60 с.
  Одноразовость **не enforce'ится** — токен можно переиграть в течение
  60 с. **Приемлемо** потому что:
  - token scope ограничен `habit_id`, чужой токен не откроет чужой стрим.
  - 60 с — слишком короткое окно для реальной атаки.
  - token не даёт ничего кроме read-only стрима.
  - `access_log off` для пути (см. §3.2) — токен не оседает в логах.
  - **Подтверждено пользователем:** без одноразовых токенов, не усложняем.

### 3.12 Разделение секретов (LOW)

- `SSE_TOKEN_SECRET` отдельный от `SERVICE_SECRET`. Принцип — разные
  URL = разные уровни доверия (см. AGENTS.md):
  - `SERVICE_SECRET` — internal-контур (`/internal/*`), между trusted
    сервисами (bot, worker, backend). Компрометация → бот может
    дергать `/internal/payments/confirm` от имени любого юзера (уже
    broken contract, см. AGENT_BOOTSTRAP §9 — отдельная задача).
  - `SSE_TOKEN_SECRET` — пользовательский контур (`/api/v1/events/*`),
    токен выдаётся по initData юзера. Компрометация → атакующий может
    слушать SSE-стримы юзеров (read-only, без действия).
  - **Смешивать опасно:** если злоумышленник узнал `SERVICE_SECRET`
    (например, через лог-утечку в bot'е), он не должен автоматически
    получить возможность выдавать себе SSE-токены для прослушки.
  - **Ротация:** если придётся ротировать `SSE_TOKEN_SECRET` (например,
    лог-утечка токена в access-логе nginx до применения `access_log off`),
    это не затронет internal-контур. И наоборот.

### 3.13 Нативный EventSource auto-reconnect vs протухший токен (HIGH, МИТИГИРОВАНО в §2.4)

**Проблема.** TTL SSE-токена 60 с — он защищает только момент открытия
соединения. Нативный `EventSource` при сетевом сбое пытается реконнектиться
автоматически и **повторно шлёт тот же URL**, включая тот же `token` в
query. Через несколько минут жизни соединения токен протухает → сервер
отвечает 401 → по спеке EventSource при не-200 **закрывается насовсем**
без дальнейших попыток реконнекта.

**Когда выстрелит.** Telegram WebView регулярно рвёт сеть:
сворачивание приложения, переключение Wi-Fi ↔ LTE, потеря фокуса
(юзер переключился в чат). Юзер откроет "Сегодня" утром, соединение
проживёт до первого сетевого сбоя (5 минут, может час) — и SSE тихо
умирает до перезахода на страницу. Классический "иногда работает,
иногда нет" — не воспроизводится на быстром ручном тестировании одной
сессии, всплывает только в проде у части юзеров.

**Митигация (в §2.4 Frontend flow):**
- `useTodayStream` **не полагается** на нативный auto-reconnect.
- На `es.onerror` сам закрывает EventSource, запрашивает свежий токен
  через `sseToken.request(habitId)`, открывает новый EventSource с
  `last_event_id=<tracked>` в query (для resume позиции в Redis Stream).
- Backoff 1s → 2s → 5s → 10s, сбрасывается на 0 при успешном событии.
- Backend дополнительно принимает `last_event_id` из query как fallback
  к `Last-Event-ID` header (нативный header шлётся только в рамках одной
  ES-инстанции, для новой инстанции с новым токеном — query).

**Объём правок:** ~30 строк в `useTodayStream.ts` (manual loop),
~5 строк в `events.py` (`last_event_id` из query), ~3 строки в
`redis_stream_bus.py` (приоритет header vs query).

---

## 4. Оценка объёма

| Слой | Новые файлы | Изменения в существующих | Примерный объём |
|---|---|---|---|
| Backend (Python) | `services/sse/{redis_stream_bus,sse_token,sse_formatter}.py`, `api/v1/events.py` | `main.py` (роутер), `core/middleware.py` (exact-path исключение), `core/config.py` (новое поле `sse_token_secret`) | ~350 строк нового, ~35 строк правок |
| Worker (Python) | `services/event_publisher.py` (с idempotency-guard через SET NX) | `tasks/process_checkin.py` (Guard 1 + Guard 2 после commit, ~20 строк) | ~110 строк нового, ~25 строк правок |
| Frontend (TS) | `shared/hooks/useTodayStream.ts` (тонкая обёртка `useEffect`+`useRef`), `shared/hooks/streamController.ts` (pure function с DI: 7 параметров, без React-зависимостей), `shared/api/sseToken.ts`, `shared/hooks/__tests__/streamController.test.ts` (11 vitest unit) | `pages/Today/TodayPage.tsx` (замена mount-invalidate на `useTodayStream(habitId)`, потом удаление mount-invalidate в `d30832a`), `shared/hooks/index.ts` (реэкспорт) | ~430 строк нового (controller 200 + hook 45 + sseToken 20 + tests 165), ~6 строк правок |
| Infra | `nginx/habit-club-sse.conf.snippet` (документация-сниппет) | `docker-compose.yml` — **без изменений**. Хост-nginx правка руками через SSH. `.env.example` — добавить `SSE_TOKEN_SECRET=` | ~35 строк сниппета + 5 строк в конфиге хоста + 3 строки в `.env.example` |
| Тесты | backend: token endpoint (sign/verify, expiry, membership 403), event publisher (SET NX guard); worker: XADD после commit (mock SET NX); frontend: useTodayStream unit (enabled Boolean, **manual reconnect с истёкшим токеном**, backoff, lastEventId resume) | — | ~360 строк |
| **Итого** | **~10 новых файлов** (включая тесты backend/worker/frontend) | **~8 файлов правок** (включая `.env.example`) | **~1830 строк** |

### Порядок выполнения (если план одобрят)

> **Не выполняется сейчас.** Это план на будущую итерацию.

1. **Backend: token + SSE endpoint** (минимальный скелет без worker'а,
   `SSE_TOKEN_SECRET` в `core/config.py`, `sse_token.py` с HS256,
   exact-path middleware-исключение, membership-check в `POST /token`).
   Локально: `GET /events/stream` возвращает ручной heartbeat без Redis.
   Проверить: токен с `habit_id=foo` от юзера без membership → 403
   `membership_not_active`. Проверить, что nginx не буферизует (curl'ом).
2. **Worker: XADD после commit в process_checkin** (Guard 1 для
   `already_exists` + Guard 2 с `SET NX EX 86400`).
   Локально: redis-cli `XREAD STREAMS sse:user:test:habit-test $` + ручной
   запуск task'а → видно событие в стриме. Повторный запуск того же
   payload → SET NX вернёт False, XADD не происходит.
3. **Backend: XREAD в SSE endpoint.**
   Локально: открыть `curl -N /events/stream?token=...` → видно событие из шага 2.
4. **Frontend: useTodayStream** с `enabled: Boolean(habitId)`,
   `sseToken.request()` через axios, **ручным** reconnect-loop (см. §3.13).
   Локально: открыть TodayPage → кликнуть чек-ин в боте → страница обновилась.
   Проверить: `habitId === undefined` → хук не делает НИЧЕГО (no token request,
   no EventSource). **Проверить reconnect:** искусственно убить соединение
   (через DevTools Network → Offline на 5 с) → соединение должно
   восстановиться само через ~1–10 с со свежим токеном; на стороне сервера
   видно в логах `POST /events/stream/token` повторно.
5. **Nginx:** применить `proxy_buffering off` + `proxy_read_timeout 3600s` +
   `access_log off` для `location /api/v1/events/stream` на хосте (руками).
   Проверить из браузера: `curl -N ... | head` показывает `: heartbeat` в
   реальном времени.
6. **Тесты:** unit + один integration (XADD → XREAD → HTTP), отдельно —
   тест idempotency (двойной вызов event_publisher → один XADD), отдельно —
   тест membership-check в token-эндпоинте (юзер не в клубе → 403).
7. **Деплой:** стандартный pipeline (`make test` → commit → rsync → build).
   Не забыть добавить `SSE_TOKEN_SECRET` в `/app/.env` (НЕ в
   `/app/infra/.env`).
8. **Документация (ритуал поддержания):** обновить
   - `docs/02-architecture.md` §2 (новый поток событий).
   - `docs/06-data-model.md` §3 (новые namespace'ы в Redis: `sse:user:*`,
     `sse_published:checkin:*`).
   - `docs/04-code-standards.md` §X (паттерн SSE endpoint + idempotency).
   - `docs/07-security-and-ops.md` §2 (новый секрет `SSE_TOKEN_SECRET`,
     разделение контуров).
   - `apps/frontend/docs/STATUS.md` (новый хук `useTodayStream`).
   - `docs/09-prod-readiness.md` §1.1 (снять 🟡 polling-mount-invalidate).

---

## 5. Открытые вопросы

### Закрыты в редакции 2

**Q1 (EventSource + initData + секрет)** — ДВУХСТУПЕНЧАТЫЙ FLOW.
`POST /events/stream/token` (initData в заголовке) →
`GET /events/stream?token=...`. Токен подписан **отдельным**
`SSE_TOKEN_SECRET` (HS256, TTL 60 с, claims `sub`, `habit_id`, `scope`,
`exp`). Не шарится с `SERVICE_SECRET` — разные контуры, разные секреты,
разная blast-radius при компрометации (см. §3.12).

**Q2 (Какие события публиковать)** — ТОЛЬКО ЧЕК-ИН.
`checkin.accepted` + `checkin.rejected` в MVP. `penalty.applied` и
`catch.received` — в v2, отдельный PR (см. §2.5).

**Q6 (Дедупликация событий)** — GUARD 1 + GUARD 2.
Guard 1 (early-skip на `reason == "already_exists"`) + Guard 2 (`SET
sse_published:checkin:{m}:{d} "1" NX EX 86400` перед XADD). Атомарный
check-and-set защищает от двойной публикации при Celery redelivery и
graceful shutdown worker'а. Trade-off: «at-most-once», приемлемо для
UI-hint событий (см. §2.3).

### Подтверждены пользователем (редакция 3)

**Q3 (Heartbeat интервал)** — 30 СЕКУНД.
`XREAD BLOCK 30000`. Подтверждено.

**Q4 (Одноразовый SSE-токен)** — НЕ ДЕЛАЕМ.
TTL 60 с + scope по `habit_id` + `access_log off` достаточно. Не
усложняем. Подтверждено.

**Q5 (Retention стрима)** — `XADD MAXLEN ~ 1000`.
Без отдельного cron'а. Подтверждено.

**Q7 (Применение nginx-сниппета)** — РУКАМИ ЧЕРЕЗ SSH.
Автоматизацию хоста не делаем в этой итерации (отдельный PR позже, если
понадобится). Подтверждено.

### Закрыты пост-ревью (редакция 5 — после Step 1+2)

**Q-RATE-LIMIT (per-user SSE concurrency) — РЕШЕНО** (`ec60c0f`).
В Q4 сознательно выбрали "не одноразовые токены", но это значит, что
один валидный токен открывает неограниченное число соединений в окне
60с — DoS-вектор. Решено per-user лимитом на конкурентные соединения
через Lua-atomic check-and-incr (`SseConnectionLimiter`, §2.6).
N=5 выбран не "с запасом", а из явного рассуждения: 1 вкладка +
3-4 клуба активного юзера + 1 дубль от reconnect-race = 5.

**Q-LAST-EVENT-ID-CONTRACT — РЕШЕНО** (`a0217ec`).
Контракт эндпоинта зафиксирован с Step 2: query-параметр
`last_event_id: str | None = Query(None)` уже в сигнатуре `stream_sse_events`.
Step 4 (XREAD) начнёт его использовать; фронт (Step 6) может слать
с первого дня без правок. Тест `test_last_event_id_query_param_accepted_in_signature`
фиксирует это в FastAPI dependant.query_params.

### Закрыты пост-ревью (редакция 7 — после Step 4)

**Q-FD-LEAK-PER-REQUEST-FROM_URL — РЕШЕНО** (внутри `7ada2ad`).
Изначальный план Step 4 задумывал `redis.asyncio.from_url(settings.redis_url)`
внутри эндпоинта `stream_sse_events`. Каждое открытие SSE-соединения
создавало бы новый connection pool; без явного `aclose()` пулы
накапливались с каждым reconnect клиента — ровно тот класс FD-exhaustion,
против которого дизайн и проектировался. Решено: async-singleton
`apps/backend/app/db/redis_async.py` + lifespan warm/close + `AsyncRedisDep`
alias + anti-regression test `TestAsyncRedisSingleton` (2 теста). См. §2.7.

**Q-MAXLEN-TRIMMED-LAST-EVENT-ID-EDGE — РЕШЕНО** (Step 4, см. §3.10).
При trim'нутом ID XREAD-цикл не откатывается на `$`, а продолжает ждать
с тем же ID. Redis нормализует start_id за пределы стрима к next-available,
поэтому следующий реальный event всё равно придёт. Реализация упростилась
(не нужна ветка проверки `last_id == "$"`), поведение для клиента то же.

### Остаются открытыми

**Нет.** Все архитектурные вопросы закрыты.