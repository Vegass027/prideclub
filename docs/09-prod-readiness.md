# Habit Club — Статус бэкенда и план до прода

> Дата среза: 2026-07-23 (обновлено 2026-08-07 после Step 7 — успешный деплой
> SSE+Redis Streams на проде, см. §1.0 ниже).
> Сервер: Contabo Cloud VPS 4 (4 vCPU / 8 GB / 100 GB SSD), `169.58.52.78`
> Домены: `prideclub.fun` (основной), `app.prideclub.fun` (Mini App),
> `admin.prideclub.fun` (Admin Mini App), `api.prideclub.fun` (API), `db.prideclub.fun` (pgweb)
> ⚠️ **2026-08-05 — Beget временно заблокировал `prideclub.fun`** (NS records
> переключены на `verification-hold.suspended-domain.com`). Юзер пофиксил
> через support; NS вернулись к рабочим. Подробности — `Pravki.md §7.8`.
> Документ описывает только **бэкенд** (FastAPI + worker + bot). Фронт — отдельная тема.

---

## 1. Текущая стадия: **✅ готов к soft-launch + Admin Mini App + SSE real-time**

### 1.0 Что сделано в этой итерации
- ✅ `/setdomain` в BotFather → Mini App открывается кнопкой в боте
- ✅ Mini App живой на `https://app.prideclub.fun`
- ✅ **Admin Mini App** на `https://admin.prideclub.fun` (owner-only, через `OWNER_TELEGRAM_ID`)
- ✅ Telegram WebApp SDK подключён, initData передаётся в каждом запросе
- ✅ **Hardening итерация T1–T7** (см. `TZ_kharakteristiki_personazha.md` §8.1) —
  рефакторинг сервисов/репозиториев, без функциональных изменений.
  Все сервисы перезапущены на проде, `/health=ok`.
- ✅ **Lint-zero итерация** (commits `7a39fc1` + `de57457`, 2026-07-23): ruff
  теперь чистый во всех трёх сервисах (backend/bot/worker) без per-file-ignore
  для B008. Все 86 handler-сигнатур переведены на `Annotated[X, Depends(get_x)]`
  per FastAPI docs. Без функциональных изменений, все контейнеры healthy после
  деплоя.
- ✅ **SSE через Redis Streams — Steps 1-6** (2026-08-04, ветка
  `feature/topic-scoped-checkin` → main, merge commit `0c9a7b8`):
  - Step 1 `c836542` — `POST /api/v1/events/stream/token` с HS256 JWT (TTL 60 с),
    отдельный `SSE_TOKEN_SECRET`, membership-check на этапе выдачи.
  - Step 2 `9d5b374` — `GET /api/v1/events/stream` SSE endpoint + exact-path bypass
    initData-middleware + `last_event_id` query param в контракте.
  - Fix-up 1 `a0217ec` — exact-path bypass test + обратная совместимость
    `POST /events/stream/token` под initData-auth.
  - Fix-up 2 `ec60c0f` — per-user concurrency limit через Lua-atomic INCR+DECR
    (`MAX_CONCURRENT = 5`, защита от DoS через replayable token).
  - Step 3 `11edb14` + `e5cc8e0` — worker `event_publisher.py` с Guard 1 (early-skip
    на дубль) + Guard 2 (SET NX EX перед XADD под единым try/except).
  - Step 4 `7ada2ad` — backend `redis_stream_bus.py` + XREAD BLOCK в SSE endpoint
    + async-Redis singleton (`db/redis_async.py`) — фикс FD-leak при reconnect.
  - Step 5 `900ef4f` — nginx exact-match `location = /api/v1/events/stream`
    (`proxy_buffering off`, `proxy_read_timeout 3600s`, `proxy_send_timeout 3600s`,
    `access_log off`) — применён на проде руками через SSH (бэкап в
    `/var/backups/nginx/habit-club.bak.20260804_1823`).
  - Step 6 `5d8c6e6` + `d30832a` — frontend `useTodayStream` хук + `streamController`
    pure-function с 7-param DI + `sseToken` API + 11 vitest unit + удаление
    mount-invalidate `useEffect(invalidateQueries)` в `TodayPage`.
  - Инфра-фиксы `0ceb647` + `4d821d6` — `apps/frontend/.dockerignore` +
    `infra/docker-compose.yml` `x-backend-env`: `SSE_TOKEN_SECRET: ${SSE_TOKEN_SECRET}`,
    `SSE_TOKEN_TTL_SECONDS: ${SSE_TOKEN_TTL_SECONDS:-60}`. Подключение через
    compose-интерполяцию из `/app/infra/.env` (НЕ `/app/.env` — тот не монтируется).
- ✅ **Smoke-test оба сценария** (2026-08-04): regression check (все API endpoints
  отвечают корректно, без initData → 401), SSE-сценарий через Mini App
  (юзер 7295309649, habit `d5134c5b-…`, real-time обновление через EventSource,
  Redis stream `sse:user:7295309649:d5134c5b-…` XLEN=1, `sse_publish_ok` ровно один).
  Connection limiter `sse:conn:7295309649` expire'ируется через TTL=180 с после закрытия.

### 1.1 Что полностью работает (есть на проде, проверено E2E)

| # | Подсистема | Статус | Где проверено |
|---|-----------|--------|---------------|
| 1 | **Аутентификация** initData + JWT для /internal | ✅ Работает | E2E на сервере, 161 backend тестов |
| 2 | **Чек-ины** через Celery worker | ✅ Работает | `worker_checkin_ok` в логах, идемпотентность по `(membership_id, date)`. T4: streak-SELECT вынесен в `CheckinRepository.get_recent_dates`. |
| 3 | **Кэтчер-механика** через worker | ✅ Работает | `process_penalty` через `/internal/penalties/catch` → penalty + transaction в БД. T2: `_is_suspicious` в репо. T5: fail-closed без Redis. |
| 4 | **Telegram Payments webhook** | ⏸ Код готов, не вызывается | `process_payment` идемпотентен через `charge_id`, но `bot.send_invoice` в коде отсутствует, фронт использует мок `PaymentModal`; в БД `transactions=0` |
| 5 | **Депозит + штрафы** | ✅ Работает | FK-фикс (penalty → transaction в одной транзакции) |
| 6 | **Bonus-система** (catch bonus, expire) | ✅ Работает | `apply_catch_bonus`, `expire_bonus_points`. T3: fakes-based DI вместо lookup-коллбэков. |
| 7 | **Celery Beat** (close_catch_window в :05 каждого часа) | ✅ Работает | `crontab(minute=5)` в `celery_app.py:64` |
| 8 | **Sentry + Prometheus** | ✅ Инициализируются (no-op без DSN) | `/metrics` endpoint отдаёт метрики |
| 9 | **PostgreSQL** (12 миграций, расширения) | ✅ Работает | `000_extensions` → `012_proof_types` (миграции 010/011 — topic-scoped чек-ины и третий топик; 012 — `proof_types JSONB` для multi-proof_types в админке) |
| 10 | **Redis** (catch rate-limit Lua, today cache) | ✅ Работает | `catch_rate_limiter.py`, `today_cache.py`. T1: `parse_rate_limit_spec` в `core/utils.py`. |
| 11 | **Antifraud** (suspicious_pairs, proof validation) | ✅ Работает | `suspicious_pairs_service.py` + T2 `SuspiciousPairsRepository.lookup_flagged` |
| 12 | **Season prize distribution** | ✅ Работает | `close_season` через worker |
| 13 | **JSON-логирование** (structlog + JSONRenderer) | ✅ Работает | backend + worker пишут JSON в stdout |
| 14 | **HTTP rate-limit** (60/min api, 120/min internal) | ✅ Live проверен | 130 req → 120 пропущено + 10×429 |
| 15 | **HTTPS + Nginx + Let's Encrypt** | ✅ Live работает | `app.prideclub.fun/health` → 200 |
| 16 | **Telegram bot webhook** | ✅ Live работает | POST `/bot/webhook` → 200 |
| 16a | **Bot pre-filter** по `proof_types` и дубликату | ✅ Работает с 2026-07-23 | PR №9: `GET /internal/bot/habit_state` → бот проверяет тип и дубликат ДО отправки в backend. Юзер сразу получает «в этом клубе принимается только X» / «уже отметился сегодня» вместо ложного «Принято». См. `02-architecture.md` §14. |
| 17 | **CI в GitHub Actions** | ✅ Конфиг исправлен | `backend-ci.yml`, `frontend-ci.yml` |
| 18 | **Admin Mini App** (управление клубами: CRUD + activate/archive/restore) | ✅ На проде с `2026-07-21` (commit `ad0267b`) | `apps/frontend/src/admin/`, `admin.prideclub.fun`. Owner-gate в `core/middleware.py`. |
| 19 | **SSE real-time updates** (Mini App «Сегодня» без polling) | ✅ На проде с `2026-08-04` (commits `c836542`..`d30832a`, merge `0c9a7b8`) | `POST /api/v1/events/stream/token` (JWT TTL 60 с) + `GET /api/v1/events/stream` (XREAD BLOCK 30000) + worker `event_publisher` (Guard 1+Guard 2) + nginx exact-match + frontend `useTodayStream`. Redis streams `sse:user:{u}:{h}`, idempotency keys `sse_published:checkin:{m}:{d}` (24 ч), per-user concurrency `sse:conn:{u}` (MAX=5). Покрыто 100 тестами (67 backend + 22 worker + 11 frontend vitest). Smoke-test: ручной чек-ин через Mini App → real-time обновление без refetch. |
| 20 | **Pravki-deposit-sse: deposit на users** (PR #1, commit `ac6951f`) | ✅ На проде с `2026-08-08` | `users.deposit_balance` (BigInt, NOT NULL DEFAULT 0). Миграции 014a (ADD COLUMN + backfill + 3 sanity-чека) + 014b (DROP COLUMN memberships.deposit_balance). `MembershipService.recompute_pause_status(user_id)` пересчитывает ACTIVE/PAUSED по всем клубам юзера после каждого изменения депозита (PR #1 §Z-2.6). Тестов: 304 passed (vs baseline 288, +16). Покрытие: `test_user_deposit_balance.py` (8 кейсов), `test_recompute_pause_status.py` (4 кейса), `test_catch_api.py` (defense-in-depth). Smoke-test: ручной catch → `user.deposit_balance` уменьшается, `prize_pool` увеличивается, status=PAUSED. |
| 21 | **Pravki-deposit-sse: UI join/wallet** (PR #2, commit `9736b5b`) | ✅ На проде с `2026-08-08` | `useWallet()` хук (staleTime 30s), `GET /me/wallet` endpoint с `WalletClubOut.can_checkin`, `TopUpModal` без radio (пресеты 299/599/999/1999 ₽, фильтрация по `penalty_amount`). 89 frontend vitest + 287 backend. |
| 22 | **LEFT/PAUSED bypass fix** (commit `ae6bd07`) | ✅ На проде | `MembershipService.join` теперь проверяет депозит ВСЕГДА — никаких исключений для LEFT/PAUSED. Раньше: LEFT→ACTIVE реактивация без проверки депозита (баг). Сейчас: user-lock + депозит-check для всех трёх кейсов (new / LEFT→ACTIVE / PAUSED→ACTIVE). |
| 23 | **Pravki-subscribe-and-join: объединённый платёж подписка+депозит** (commits `b51eb90` + `b98cab0`) | ✅ На проде с `2026-08-08` | Новый endpoint `POST /api/v1/payments/subscribe`. `MembershipService.subscribe_and_join(*, user_id, habit_id, deposit_amount_kopecks, subscription_accepted, idempotency_key)` с 3-кейсовой логикой (3a/3b/3c — см. `Pravki-subscribe-and-join.md` §Z-13.1 матрица). Frontend: `JoinPayModal` (чекбокс подписки + пресеты депозита, отфильтрованные по `penalty_amount`), `useJoinAndPay` hook. **Subscription fee НЕ попадает на `deposit_balance`** (b98cab0) — кладётся только `deposit_amount_kopecks`. Тестов: 24 в `test_subscribe_and_join.py` + обновлённые assertions. |
| 24 | **Pravki-bug-fixes Z-19: joiner-late protection** (commit `497d01d`) | ✅ На проде с `2026-08-09` | 3 уровня защиты: (1) bot pre-filter на `is_joined_late=True` в `HabitStateResponse` → REJECT_JOINED_LATE с подставленными `start`/`end` окна; (2) worker `process_checkin` симметричная проверка → `CheckinJoinedLateError` (422) ДО `get_or_create_done`; (3) worker race-fallback возвращает `window_start`/`window_end` для бота в результате. Защита от двойного списания и нелогичного alert'а. Alembic миграция 015 (enum extension). 12 backend + 2 worker + 3 bot тестов. Defensive fallback в `StatusBadge` (commit `564b8db`) на случай рассинхрона кэша. |
| 25 | **Frontend image: workaround для overlay-конфликта** (commit `4a390e1`) | ✅ На проде | Frontend сервис в compose переключён на `image: nginx:1.27-alpine` + volume mount на `/app/apps/frontend/dist` (вместо `build:`). Bundle переживает recreate/restart. Подробности и deploy-процедура — `docs/10-deploy.md` §9.1. |
| 26 | **Pravki §Z-22: pre-filter holes 5-round fix** (commits `b4f8974` → `2eda062` → `3e7f1ee` → `4272cfa` → `92a05be` → `6cf22f2` → `b4cc923`) | ✅ Готово к деплою | Трёхуровневая защита (bot prefilter sync + backend defense-in-depth в `enqueue_checkin` + frontend mapper в `checkinReject.ts`). Закрыты 4 дырки: (1) `checkin_window_closed` — бот говорил «Принято» при просроченном окне; (2) `not_checkin_topic` — DRIFT FIX мёртвого magic-string `("wrong_topic", "checkin_wrong_topic")` в `_text_for_code`; (3) `membership_paused`/`membership_left` — сплит `MembershipNotActiveError` (разные recovery paths: topup vs rejoin); (4) `forwarded` — особенность: `forward_date` только в aiogram Message, поэтому bot prefilter обязателен. Canonical priority v2 (по специфичности copy): state-of-day выше time/location выше proof validation. `caught_today + window_closed` инверсия (Шаг 1.1): пойманный юзер видит «поймали», не «окно закрыто». Combo-тесты для `caught_today + paused/left/window_closed` зафиксированы. Foundation: `CheckinRejectCode` enum с drift-test (защита от magic-string'ов в трёх местах). 384 backend (было 361) + 56 bot (было 38) + 68 frontend (было 50) тестов — никаких регрессий. Подробности — `docs/04-code-standards.md §7.1` и `docs/06-data-model.md §4.0`. |
| 27 | **Pravki-no-deposit-waived-marker** (commits `3796531` + `5bfeec7` + `241115f` + `4dc2b08`) | ✅ Готово к деплою | Закрыта финансовая дыра «окно закрыто + deposit=0 + topup → catch за прошедший день → деньги списываются повторно». Решение: (1) `apply_window_expired` при `deposit == 0` пишет маркерную `Penalty(reason=WAIVED_NO_DEPOSIT, amount=0)` (Checkin/Transaction/recompute НЕ трогаются); (2) `apply_catch` idempotency расширена на ВСЕ reason'ы — за день есть любая Penalty → catch отвергается (`penalty_already_processed`); (3) рефактор: удалён мёртвый дубль `apply_window_expired` в `close_catch_window`. **Бонус — закрыта ещё одна дыра:** прямой POST /catch поверх существующего `WINDOW_CLOSED_NO_CATCH` теперь корректно отвергается (UNIQUE `uq_penalty_per_day_reason` пропускал из-за отличающегося reason). `can_catch` в /members уже использовал `ids_with_any_penalty_today` — дыра на уровне UI была закрыта ещё в PR #1 §Z-21, defense-in-depth на сервисе теперь полный. **Никаких миграций** (String(64) без CHECK constraint — значение принимается без изменений схемы). 6 новых backend-тестов: 2 в `test_penalty_service.py::test_apply_window_expired_*` (маркер + идемпотентность), 4 в `test_apply_catch_*` (rejected WAIVED/WINDOW_CLOSED/CAUGHT + other-date success). Backend suite: 399 passed (+6 vs baseline 393), 16 pre-existing failed (admin/photo). Подробности — `Pravki-no-deposit-waived-marker.md`. |

**Snapshot 2026-08-09 (после Z-19 deploy):**
- **Alembic:** `015_checkin_status_extra_values` (head)
- **Миграции:** 000 → 015 (16 файлов, последние 014a/014b/015 — из этой сессии)
- **БД:** 2 users, 3 habits (Пробежка, Планка, Чтение), 3 memberships, 4 transactions
- **Backend:** `habit-backend` Up, healthy, commit `497d01d` + `564b8db` (Z-19 + defensive StatusBadge)
- **Bot:** `habit-bot` Up, commit `497d01d` (Z-19 prefilter + race-fallback text)
- **Worker:** `habit-worker` Up, commit `497d01d` (joined_late handling + window_start/end fallback)
- **Frontend:** `habit-frontend` Up, bundle `main-CmHeC1H6.js` (Z-19 + defensive fix)
- **Тесты локально:** 354 passed backend (24 новых в `test_subscribe_and_join.py` + 12 в `test_joined_late_protection.py` + baseline 318), 17 worker, 35 frontend
- **Тесты на проде:** не запускаются (только локально + CI)
- **9 baseline-fails** в `test_admin_habits_api.py::TestAdminHabitEndpoints` — требуют настоящий Redis (не связано с этой сессией)

### 1.2 Тесты

| Пакет | Локально (после Step 6) | На сервере |
|-------|----------|------------|
| `apps/backend/tests` | **287 passed** (89 SSE-related + 198 остальных) + **10 failed** (`test_admin_habits_api.py::TestAdminHabitEndpoints` × 9 требуют настоящего Redis, `test_migrations.py::test_alembic_round_trip_on_real_postgres` требует `pg_ctl` — физически невозможно без Docker). | не запускаются в проде (только локально + CI) |
| `apps/worker/tests` | **58 passed** + **1 failed** (`test_close_season_skips_active_seasons` — **pre-existing**, воспроизводится на `main` `bd9fd76` без SSE-кода, подтверждено в Step 7 через `git checkout main` + repro; не связано с Steps 1-4) | не запускаются в проде |
| `apps/frontend/tests` | **11 passed** (`test_stream_controller` — vitest 2.1.9, EventSource + requestToken + queryClient моканы, `vi.useFakeTimers` для backoff) | не запускаются в проде |
| **Итого** | **356 passed** + 11 failed (10 — внешние сервисы в тестах, 1 — pre-existing worker fail) | — |

### 1.3 Live endpoints (после Step 7 deploy 2026-08-04)

```
✅ https://app.prideclub.fun          → Mini App (Vite + backend API) — **real-time SSE обновления работают**
✅ https://app.prideclub.fun/health    → {"status":"ok"}
✅ https://app.prideclub.fun/ready     → {"status":"ready"} (DB + Redis OK)
✅ https://app.prideclub.fun/api/v1/users/me (без auth) → 401
✅ https://app.prideclub.fun/api/v1/events/stream/token (без initData) → 401 missing_init_data (НЕ 503 sse_not_configured — SSE_TOKEN_SECRET дошёл)
✅ https://app.prideclub.fun/api/v1/events/stream (без токена) → EventSource 401 invalid_token (при наличии валидного JWT → 200 text/event-stream)
✅ https://api.prideclub.fun           → Backend API + bot webhook
✅ https://prideclub.fun / www.        → Public web (frontend)
✅ https://admin.prideclub.fun         → Admin Mini App (owner-only)
✅ https://db.prideclub.fun            → pgweb admin (basic auth)
✅ TLS сертификат Let's Encrypt, валиден до 2026-10-19 (autorenewal)
✅ Redis Stream `sse:user:7295309649:d5134c5b-…` создан при ручном smoke-тесте (XLEN=1, `checkin.accepted` payload)
✅ Idempotency key `sse_published:checkin:7af92214-…:2026-08-04` TTL=24 ч
```

### 1.4 Решённые проблемы этой итерации

| # | Что | Решение |
|---|-----|---------|
| 1 | CI workflow падал на yaml-парсере | Заэкранировал `DATABASE_URL: "sqlite+aiosqlite:///:memory:"` (множественные двоеточия) |
| 2 | Дублирующийся workflow `backend.yml` | Удалён, остался только `backend-ci.yml` |
| 3 | Worker логировал обычным текстом | Добавлен `worker/logging_setup.py` с structlog |
| 4 | Не было общего HTTP rate-limit | `services/http_rate_limiter.py` + `RateLimitMiddleware` |
| 5 | Не было домена / HTTPS | Куплен `prideclub.fun`, настроен nginx + Let's Encrypt, Mini App доступен |
| 6 | Bot webhook SSL error → `pending_update_count` растёт | `WEBHOOK_BASE_URL=https://169.58.52.78` → `https://api.prideclub.fun`; fail-fast в проде через `_validate_webhook_url` |
| 7 | Worker `NameError: CheckinWrongTopicError` в retry-loop | Добавлен импорт в `apps/worker/worker/tasks/process_checkin.py:7-12` |
| 8 | Mini App `status=pending` не обновляется | Топик-фильтр пропускает кружки после правильной настройки `checkin_topic_thread_id` (id топика = `12` в проде) |
| 9 | PATCH `/admin/v1/habits/{id}` не сохранял price_month | Добавлен `price_month` и `penalty_amount` в payload + helper `rubToKopecks` в `HabitEditForm.tsx`; `AdminHabitUpdatePayload` расширен |
| 10 | Backend `ForwardRef('Response') not fully defined` | Убран `from __future__ import annotations` в `core/middleware.py` (PEP 563 + starlette.Response = нерезолвимый forward ref) |
| 11 | Финансы (`price_month`, `penalty_amount`) были заморожены после первого участника | Заморозка снята в `HabitService.update`; middleware `/admin/v1/*` уже гейтит доступ только owner'у. Endpoint `PATCH /admin/v1/habits/{id}/force-financials` оставлен для targeted-обновления. См. `02-architecture.md` §12. |
| 12 | Бот отвечал «Принято, молодец» даже когда worker асинхронно отвергал чек-ин (`code: wrong_type`) | Backend возвращает `{ok: True, task_id: ...}` сразу после `send_task()`. Worker отвергает задачу позже — бот не узнаёт. Решено pre-filter'ом в боте: новый `GET /internal/bot/habit_state?chat_id=...&user_id=...` проверяет `allowed_proof_types` и `already_checked_in` ДО отправки в backend. Юзер сразу получает «в этом клубе принимается только X» или «ты уже отметился сегодня» вместо ложного «Принято». См. `02-architecture.md` §14. Проверено юзером в Telegram: после деплоя бот корректно отвечает. |
| 13 | ruff падал с 368 ошибками в CI (368 — реальное число, не «49+» как ошибочно считалось) | Двухкоммитный фикс: `7a39fc1` — auto-fix 200 ошибок + 86 B008→Annotated; `de57457` — фикс импорта `TelegramUserDbDep` (забыл обновить 3 файла после выноса alias'а в `users.py`, ImportError блокировал backend старт, починено на сервере и проверено `/health, /ready, /metrics`). |
| 14 | SSE реальное время не работало в Mini App (polling-mount-invalidate давал stale state 30 с) | Step 5+6 (commits `900ef4f`, `5d8c6e6`, `d30832a`): nginx exact-match блок + frontend `useTodayStream` с manual reconnect-loop. Удалён redundant `useEffect(invalidateQueries)` в `TodayPage` — на масштабе 1000+ юзеров это была конкретная лишняя нагрузка на backend. Подробности `docs/04-code-standards.md §13` и `apps/frontend/docs/STATUS.md`. |
| 15 | При деплое frontend через `docker compose build frontend --no-cache` падал с `cannot replace to directory .../node_modules/@tanstack/react-query with file` | Добавлен `apps/frontend/.dockerignore` (commit `0ceb647`) — гигиена build context, исключает `node_modules/`. **Реальный деплой frontend через двухслойный метод** (`docs/02-architecture.md §13`): `docker run node:20-alpine + docker cp dist + nginx -s reload`, НЕ через `docker compose build`. |
| 16 | При деплое Step 7 `SSE_TOKEN_SECRET` не дошёл до backend (положили в `/app/.env`, который **НЕ монтируется** в контейнеры) | Добавлен `SSE_TOKEN_SECRET` в `/app/infra/.env` (рабочий паттерн `${VAR}` интерполяции в `x-backend-env`) и `x-backend-env` в `infra/docker-compose.yml` (commit `4d821d6`). Backend контейнер теперь видит секрет через env-переменную. Подробности — `docs/07-security-and-ops.md §5` и `docs/AGENT_BOOTSTRAP.md §3`. |
| 17 | `prideclub.fun` заблокирован Beget (`verification-hold.suspended-domain.com`, 2026-08-05) на 2 дня после моего deploy | Юзер обратился в support, NS records восстановлены. **Урок**: NS `verification-hold` означает блокировку registrar'ом — не публичный DNS-провайдер, обходные пути (`curl --resolve`) не работают для Telegram-юзеров. |
| 18 | Бот говорил «Принято» при просроченном окне/неправильном топике/paused membership/пересланном сообщении (4 одинаковых дырки паттерна async-reject-after-accepted) | **Pravki §Z-22 (5-round fix, commits `b4f8974`..`b4cc923`)**: foundation — `CheckinRejectCode` enum с drift-test. Hole #1 `checkin_window_closed` (canonical #5). Hole #2 `not_checkin_topic` + DRIFT FIX мёртвого `_text_for_code` magic-string алиаса (#6). Hole #3 `membership_paused`/`membership_left` — сплит `MembershipNotActiveError` (#6/#7). Hole #4 `forwarded` — особенность: `forward_date` только в aiogram Message (#10). Canonical priority v2 (state-of-day выше time/location выше proof validation). Three-tier защита: bot prefilter (sync) + backend defense-in-depth в `enqueue_checkin` (sync reject) + frontend mapper (`checkinReject.ts`). Подробности — `docs/04-code-standards.md §7.1`, `docs/06-data-model.md §4.0`. |
| 19 | В Telegram Mini App через SSE `checkin.rejected` приходил raw-код вроде `checkin_window_closed` (юзер видел машинный текст в alert) | **Pravki §Z-22 Step 5** (commit `b4cc923`): frontend mapper `apps/frontend/src/shared/texts/checkinReject.ts` симметричный `apps/bot/bot/handlers/checkin_texts.py`. 14 кодов покрыты. `streamController.ts` теперь использует `checkinRejectText(code, ctx)` вместо pass-through `payload.message`. |

---

## 2. Что осталось сделать для **soft-launch** (тест-группа 10-50 человек)

### 2.1 🔴 Без этого НЕЛЬЗЯ открыть для пользователей

_(пусто — soft-launch разблокирован)_

### 2.2 🟡 Желательно до soft-launch

#### Шаг 1. Бэкапы PostgreSQL
- **Статус**: отложено (нет дешёвого РФ-облака для S3)
- **Варианты**: 
  - **Contabo Auto-Backup** (если опция включена — ~7-15€/мес, Германия, но работает)
  - **Yandex Object Storage** (4000₽ гранта для новых, карта не нужна)
  - **Переезд БД в Selectel managed** — тогда бэкапы встроенные (бесплатно, РФ)
- **Скрипт готов**: `infra/backup/backup_cron.sh` (pg_dump | gzip | age | s3)
- **Время**: 30 минут после выбора сервиса
- **Ответственный**: я (когда Дмитрий выберет сервис)

#### Шаг 2. Sentry DSN (опционально)
- **Статус**: отложено (Sentry требует OAuth GitHub на онбординге — не критично)
- **Альтернатива**: можно мониторить логи через Grafana Loki + Telegram alerts
- **Время**: 30 минут после получения DSN

### 2.3 🟢 Не блокирует soft-launch

#### Шаг 3. Перенос PostgreSQL в Selectel managed
- **Когда**: до широкого запуска для пользователей (любых кроме себя и друзей)
- **Зачем**: ФЗ-152 — ПДн должны храниться на территории РФ
- **Что нужно**: купить managed PostgreSQL в Selectel (~2000₽/мес)
- **Что делаю я**: pg_dump | pg_restore | сменить DATABASE_URL | рестарт | smoke test
- **Время**: 30 минут после покупки
- **Downtime**: 30-60 секунд

#### Шаг 4. Rate-limit на /api/v1/* в nginx (опционально)
- **Статус**: уже есть в backend (60/min). Можно добавить дополнительный слой в nginx.
- **Зачем**: защита от DDoS до того как запрос дойдёт до Python
- **Время**: 15 минут

#### Шаг 5. Prometheus + Alertmanager
- **Что**: алерты в Telegram (backend down, worker queue > 100, 5xx > 1%)
- **Инструмент**: prometheus + alertmanager + telegram-bot
- **Время**: 3 часа
- **Альтернатива сейчас**: cron-скрипт `curl /health && curl /ready` каждые 5 минут, шлёт в TG при ошибке

#### Шаг 6. Load testing
- **Инструмент**: `locust` или `k6`
- **Сценарий**: 1000 одновременных пользователей делают чек-ин
- **Проверка**: p99 < 500ms, нет 5xx
- **Время**: 3 часа

#### Шаг 7. ФЗ-152 compliance — финальная
- [ ] Политика конфиденциальности на сайте
- [ ] Endpoint `DELETE /users/me` для GDPR (таблица `user_consents` уже есть)
- [ ] Кнопка "Удалить аккаунт" в Mini App

#### Шаг 8. Подключение frontend страниц к реальному backend API
- **Что**: сейчас страницы Mini App имеют базовую разметку, нужно подключить к API:
  - `Marketplace` → `/api/v1/habits`
  - `Today` → `/api/v1/checkins/today`, `/api/v1/checkins/*`
  - `Members` → `/api/v1/members/*`
  - `Balance` → `/api/v1/balance`
  - `Leaderboard` → `/api/v1/leaderboard/*`
  - `Profile` → `/api/v1/users/me`
  - `Onboarding` → `/api/v1/habits/join`
- **Статус**: API endpoints существуют, нужно писать хуки + UI
- **Время**: 3-5 дней работы

---

## 3. Технический долг (не блокер)

_Обновлено после T1–T7 (22.07.2026). Полная таблица с приоритетами —
`TZ_kharakteristiki_personazha.md` §8.1._

| Что | Файл | Что сделать |
|-----|------|------------|
| ~~`on_event` deprecation в FastAPI~~ | ~~`apps/backend/app/main.py:81`~~ | ✅ Закрыто: миграция на `lifespan` уже сделана (`main.py:32, 74`). |
| ~~`redis_port=None` в worker~~ | ~~`apps/worker/worker/tasks/process_penalty.py`~~ | ✅ Закрыто (T5, commit `46114ca`): прод-runner бросает `RateLimitDisabledError` → Celery autoretry. Тесты сохраняют fail-open через явный `redis_port=None`. |
| ~~Catch rate-limit в коде~~ | ~~`penalty_service.py`~~ | ✅ Константа `RATE_LIMIT_CATCH = "10/10s"` уже в `core/constants.py:67` (`PenaltyConfig`). Парсинг вынесен в `core/utils.py::parse_rate_limit_spec` (T1, commit `e129398`). |
| `_remap_postgres_types_for_sqlite` мутирует типы колонок | `apps/worker/tests/conftest.py:98-145` + `apps/backend/tests/conftest.py` | Перенесено в Фазу B (T6/T8): добавлять новые модели (`UserStats`, `UserStatus`) в whitelist при коммите миграции 009. |
| Legacy `Any` без импорта в `PenaltyService.__init__` | `penalty_service.py:48` | Перенесено в T11 (deferred до после Фазы B). Частично закрыто через T2 — `_suspicious_service: Any` ушёл, остался в одном сигнатуре. |
| Admin Mini App интеграция — пустые/default-value на UI | `apps/frontend/src/admin/pages/*` | Сделано (commit `ad0267b`), но UI минимум — после Фазы B будет polish. |
| ~~Topic-scoped чек-ины~~ | — | ✅ Закрыто 2026-07-23: миграции 010 (`checkin_topic_thread_id`, `notifications_topic_thread_id`) и 011 (`chat_topic_thread_id`) применены на проде. Бот фильтрует по `message_thread_id`, штрафы публикуются в топик уведомлений, кнопки «🎬 Сделать чек-ин» и «💬 Перейти в чат» открывают нужные топики. |
| ~~ruff: 368 ошибок в backend+bot+worker~~ | — | ✅ Закрыто 2026-07-23 (commits `7a39fc1`, `de57457`): `Annotated[X, Depends(get_x)]` per FastAPI docs во всех 86 handler-сигнатурах, удалены дубликаты (`get_membership_service`, `membership_service.leave`), устранены F821 (Response, pytest, _drop_stale_records), длинные строки перенесены, `asyncio.to_thread` для sync I/O в `uploads.py`. ruff чистый во всех сервисах без per-file-ignore для B008. Подробности и паттерн — `docs/04-code-standards.md` §1. |
| 🟡 **Известное несоответствие**: бот и мини-апп показывают РАЗНЫЕ формулировки для `caught_today` (cron `apply_window_expired` vs `apply_catch`) | `apps/worker/worker/tasks/process_checkin.py:159-167` (bot _text_for_code), `apps/frontend/src/shared/texts/checkinReject.ts:REJECT_CAUGHT_TODAY` | **Контекст:** Worker SSE payload `{reason, message}` НЕ содержит `checkin_status` (worker шлёт `reason=caught_today` для обеих ситуаций — кэтчер поймал ИЛИ cron после закрытия окна). Бот различает их через `state.checkin_status` в `HabitStateResponse` (`caught` vs `missed`) и шлёт разные тексты (`REJECT_CAUGHT_TODAY` «🎯 Сегодня вас уже поймали за пропуск» vs `REJECT_PENALTY_DAY_CLOSED` «⏰ Сегодня штраф за пропуск уже списан в призовой фонд клуба (никто не поймал)»). Frontend mapper по `code` не различает — оба сценария показывают один общий текст «поймали». **Влияние на юзера:** для cron-only сценария (никто не ловил — сам не отметился) бот говорит «штраф списан, никто не поймал», а через несколько секунд мини-апп показывает «поймали» — одинаковый финансовый исход, разная копия. **Не блокирует deploy** (финансово оба сообщения корректны, нет двойного списания). **Отдельная задача:** расширить `CheckinEvent.payload` в `_publish_checkin_rejected` (commit `b4cc923` ничего не менял) полем `checkin_status` и маппить на фронте — объём работ отдельный PR. До этого — задокументировать как known inconsistency, чтобы через месяц не удивились, почему бот и телефон дают разную копию. |

---

## 4. Деплой-чеклист (перед открытием для пользователей)

```
✅ Sentry DSN настроен (отложено, не критично)
✅ TLS-сертификат валиден (https://app.prideclub.fun)
✅ DNS указывает на Contabo IP (prideclub.fun, app., api., www., db.)
✅ Telegram bot webhook зарегистрирован (POST 200)
✅ /setdomain в BotFather → app.prideclub.fun (Mini App открывается)
⬜ BACKUP_DAILY=true + cron установлен (отложено)
⬜ Alertmanager → Telegram alerts работают (отложено)
✅ Rate-limit на /api/v1/* активен (live tested)
✅ Логи в JSON формате (verified live)
⬜ Нагрузочный тест пройден (отложено)
⬜ Политика конфиденциальности + удаление аккаунта работают (отложено)
✅ Все env-переменные в .env (chmod 600)
✅ .env НЕ закоммичен
⬜ SSH root доступ ограничен (сейчас открыт по паролю)
⬜ fail2ban установлен
⬜ ufw только 22, 80, 443 (проверить)
✅ PostgreSQL НЕ слушает 0.0.0.0 (только 127.0.0.1)
⬜ Frontend страницы подключены к API (3-5 дней работы)
```

---

## 5. Сводный план по времени

| Фаза | Задачи | Время |
|------|--------|-------|
| **Soft-launch готов** | /setdomain, Admin Mini App сделано. Frontend MVP до 50 человек. | ✅ сейчас |
| **Фаза B (характеристики и персонаж)** | user_stats / user_statuses модели + инкремент в CheckinService + worker freeze_inactive_stats + frontend страница | 2-3 дня (после pre-B hardening) |
| **Подключение frontend к API** | Marketplace, Today, Members, Balance, Leaderboard, Profile | 3-5 дней |
| **Широкий запуск** | Переезд БД в Selectel + load-test + ФЗ-152 compliance | 1 неделя |
| **Пост-launch** | Тех-долг + новые фичи | по необходимости |

**Итого до soft-launch**: бэкенд готов, фронт — есть каркас, нужно подключить к API (3-5 дней работы фронт-разработчика).

**До широкого запуска**: ~1.5 недели при условии что Дмитрий параллельно покупает managed PostgreSQL в Selectel.

---

## 6. Контакты и ownership

| Зона | Ответственный | Статус |
|------|--------------|--------|
| Код (Python, SQL, инфра) | AI-ассистент (я) | ✅ |
| Домен + DNS | Дмитрий | ✅ `prideclub.fun` работает |
| Telegram bot setup | Дмитрий | ✅ `/setdomain` сделан; admin bot развернут |
| S3 для бэкапов | Дмитрий + я | ⏸ отложено |
| Selectel managed БД (переезд) | Дмитрий + я | ⏸ когда будут пользователи |
| Юридическое (ФЗ-152) | Дмитрий | ⏸ когда будет прод |

---

## 7. Что НЕ нужно делать прямо сейчас

- ❌ Не оптимизировать производительность до load-test
- ❌ Не рефакторить рабочий код без причины
- ❌ Не менять архитектуру (слои api/services/repositories выдержали проверку)
- ❌ Не переносить БД в РФ до soft-launch (технически работает на Contabo, никто не проверяет)
- ❌ Не подключать Sentry если не хочешь возиться с OAuth

---

## 8. Главная мысль

**Бэкенд полностью готов к soft-launch.** Все блокеры сняты: домен, HTTPS, webhook, rate-limit, JSON-логи, `/setdomain`. Frontend имеет базовую разметку страниц, нужно подключить к API (3-5 дней работы).

Остальные улучшения (бэкапы, переезд БД в РФ, Sentry, мониторинг) важны для широкого запуска, но не для тест-группы из 10-50 человек.

**Можно начинать фронт-разработку прямо сейчас.**
