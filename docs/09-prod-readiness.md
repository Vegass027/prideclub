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
| 6 | ~~**Bonus-система** (catch bonus, expire)~~ | ❌ **REMOVED Phase 8 (2026-08-21)** | Виртуальные очки удалены миграцией 018 — см. EXECUTION-PLAN-2026-08-19.md §Phase 8. Ловец теперь получает реальные деньги через `Penalty.catcher_amount` (Phase 1 Task 1.3). |
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
| 27 | **Pravki-no-deposit-waived-marker** (commits `3796531` + `5bfeec7` + `241115f` + `4dc2b08` + `9c32d6f` + `9fe3fbc` + `fffa7c4`) | ✅ На проде с `2026-08-17` (HEAD `fffa7c4`) | Закрыта финансовая дыра «юзер на паузе без денег → пополнил → catch за прошедший день → деньги списываются повторно». Решение (по коммитам): (1) **`5bfeec7`**: `apply_window_expired` при `deposit == 0` пишет маркерную `Penalty(reason=WAIVED_UNABLE_TO_PAY, amount=0)` для редкого ACTIVE+deposit=0; (2) **`241115f`**: `apply_catch` idempotency расширена на ВСЕ reason'ы — за день есть любая Penalty → catch отвергается (`penalty_already_processed`). Бонус — закрыта ещё одна дыра: прямой POST /catch поверх существующего `WINDOW_CLOSED_NO_CATCH` теперь корректно отвергается (UNIQUE `uq_penalty_per_day_reason` не срабатывал из-за отличающегося reason); (3) **`4dc2b08`**: рефактор — удалён мёртвый дубль `apply_window_expired` в `close_catch_window`; (4) **`9c32d6f` (коммит A, основное)**: rename enum → `WAIVED_UNABLE_TO_PAY` (Wide-семантика: покрывает и deposit=0, и частичный deposit < penalty); новый метод `PenaltyService.mark_waived_unable_to_pay` для `status=PAUSED` юзеров (основной путь, который раньше skip'ался фильтром `if status != ACTIVE: continue`); `close_catch_window._close_for_habit` теперь ветвится по статусу и инкрементит счётчик `waived` в summary (observability без ручного SQL). (5) **`9fe3fbc`**: docs — синхронизация 4 дока (04/06/09/Pravki) с фактическим состоянием, добавлен одноразовый E2E-скрипт. (6) **`fffa7c4`**: chore — удалён E2E-скрипт после успешного прогона на реальных данных. `can_catch` в /members уже использовал `ids_with_any_penalty_today` — defense-in-depth на сервисе теперь полный. **Никаких миграций** (String(64) без CHECK constraint, и `reason` — VARCHAR, не ENUM; 0 строк в проде с этим reason на момент коммита). 11 backend-тестов: 5 новых для `mark_waived_unable_to_pay` + 6 предыдущих (2 `apply_window_expired_*`, 4 `apply_catch_*`). Backend suite: 404 passed (+1... (line truncated to 2000 chars) |
| 28 | **Pravki-subscription-2026-08-17: блокировка чек-инов по `subscription_until` + smart renew + бейдж** (commits `9c96320` + `3240b83` + `9842d4e` + `abde0d3`) | ✅ Готово к деплою (HEAD `abde0d3`) | Закрыта дыра «поле `subscription_until` существует в БД, обновляется при оплате, но никто не проверяет в моменте». До этой серии юзер с истёкшей подпиской продолжал делать чек-ины и быть целью catch. Решение (без cron, без нового статуса LEFT — модель «на лету»): (1) **Smart renew** (`9c96320`): `MembershipService.subscribe_and_join` допускает `deposit_amount_kopecks=0..N` если итоговый баланс `(user.deposit_balance + +) >= habit.penalty_amount`. Pydantic `gt=0` → `ge=0`. Кейс 3b (активная подписка) — без изменений. (2) **4 уровня defense-in-depth** (`3240b83`): `CheckinRejectCode.SUBSCRIPTION_EXPIRED` в категории III **первым** (canonical #6, выше PAUSED/LEFT) — «продли подписку» лечит и подписку, и возможный PAUSED через recompute; «пополни депозит» лечит ТОЛЬКО PAUSED → зацикливание. Гейты: bot prefilter (sync) + backend `internal_checkins` defense-in-depth + worker `CheckinService.process_checkin` race-fallback + `PenaltyService.apply_catch` victim-side (после `lock_for_update + refresh`). Все сравнения по `habit.club_date(now_utc)` (TZ клуба), без grace period (Q2: `subscription_until == club_date` → ещё валиден). Новый `CheckinSubscriptionExpiredError` (422). (3) **Bot prefilter** (`9842d4e`): новая ветка между `is_joined_late` (#5м) и `membership_status=paused` (#7м). `HabitStateResponse.subscription_until` + `club_today` + `habit_title` добавлены. Текст `REJECT_SUBSCRIPTION_EXPIRED` в `checkin_texts.py`. (4) **Frontend 3-state badge** (`abde0d3`): новые `clubDate.ts` (vanilla `Intl.DateTimeFormat`, без `date-fns`), `subscriptionState.ts` с формулой `daysLeft = calendarDiff + 1` (день истечения включительно — Q2 день-в-день), `SubscriptionBadge` компонент (sm/md размеры). `JoinPayModal` получил 3-й режим `renew-only` для smart renew (без чекбокса подписки, без выбора депозита, отправляет `deposit_amount_kopecks=0`). ProfilePage показывает бейдж подписки рядом с бейджем «пауза» (разные сигналы). TodayPage — баннер soon/expired с кнопкой «🔄 Продлить подписку». Тестов: backend +10 (5 smart renew + 5 gates), bot +4 (prefilter branch + 3 combo), frontend +20 (SubscriptionBadge 6 + clubDate 5 + subscriptionState 9). Подробности — `Pravki-subscription-2026-08-17.md`. **Никаких миграций** (используется существующее `memberships.subscription_until`). | Закрыта финансовая дыра «юзер на паузе без денег → пополнил → catch за прошедший день → деньги списываются повторно». Решение (по коммитам): (1) **`5bfeec7`**: `apply_window_expired` при `deposit == 0` пишет маркерную `Penalty(reason=WAIVED_UNABLE_TO_PAY, amount=0)` для редкого ACTIVE+deposit=0; (2) **`241115f`**: `apply_catch` idempotency расширена на ВСЕ reason'ы — за день есть любая Penalty → catch отвергается (`penalty_already_processed`). Бонус — закрыта ещё одна дыра: прямой POST /catch поверх существующего `WINDOW_CLOSED_NO_CATCH` теперь корректно отвергается (UNIQUE `uq_penalty_per_day_reason` не срабатывал из-за отличающегося reason); (3) **`4dc2b08`**: рефактор — удалён мёртвый дубль `apply_window_expired` в `close_catch_window`; (4) **`9c32d6f` (коммит A, основное)**: rename enum → `WAIVED_UNABLE_TO_PAY` (Wide-семантика: покрывает и deposit=0, и частичный deposit < penalty); новый метод `PenaltyService.mark_waived_unable_to_pay` для `status=PAUSED` юзеров (основной путь, который раньше skip'ался фильтром `if status != ACTIVE: continue`); `close_catch_window._close_for_habit` теперь ветвится по статусу и инкрементит счётчик `waived` в summary (observability без ручного SQL). (5) **`9fe3fbc`**: docs — синхронизация 4 дока (04/06/09/Pravki) с фактическим состоянием, добавлен одноразовый E2E-скрипт. (6) **`fffa7c4`**: chore — удалён E2E-скрипт после успешного прогона на реальных данных. `can_catch` в /members уже использовал `ids_with_any_penalty_today` — defense-in-depth на сервисе теперь полный. **Никаких миграций** (String(64) без CHECK constraint, и `reason` — VARCHAR, не ENUM; 0 строк в проде с этим reason на момент коммита). 11 backend-тестов: 5 новых для `mark_waived_unable_to_pay` + 6 предыдущих (2 `apply_window_expired_*`, 4 `apply_catch_*`). Backend suite: 404 passed (+11 vs baseline 393), 16 pre-existing failed (admin/photo). **E2E на реальных данных Софьи (id=5361424459) PASSED 2026-08-17 13:09 UTC:** PAUSED+deposit=0 → cron → 2 WAIVED маркера (клубы с закрытым окном; третий клуб 'Чтение' `skipped: window_open` — by design); topup → ACTIVE → catch за день → `PenaltyAlreadyProcessedError(code=penalty_already_processed)`; депозит не списан. **Точка отката:** `eaffd9d` (pre-PR, безопасный полный откат; промежуточные состояния небезопасны — см. `Pravki-no-deposit-waived-marker.md` §Rollback). Подробности — `Pravki-no-deposit-waived-marker.md`. |
| 29 | **Pravki-manual-catch-2026-08-18 §Шаг 3: переход на ручную ловлю** (commits `f4eb243` + `48210e9` + `1b1d325` + `3b81327`, feature branch `feature/qa-batch-2026-08-14`) | ✅ Готово к деплою, деплой НЕ выполнялся (ожидает push + rollout) | **Переход от авто-списания к ручной поимке** (Pravki-manual-catch-2026-08-18.md §Шаг 3): **(1)** `Habit.catch_window_end(club_date)` + `Habit.is_within_catch_window(now_utc, club_date)` (Шаг 1, `f4eb243`) — fix багов #13 (midnight windows), `CATCH_WINDOW_BUFFER_HOURS=2` (вместо `+1h`). **(2)** Race-free `apply_catch` (Шаг 2, `48210e9`) — `now_utc` резолвится ПОД `lock_for_update(user)` чтобы избежать «ловли после границы» гонки. `CatchWindowClosedError(422)`. **(3)** `apply_window_expired` + `mark_waived_unable_to_pay` → safe no-op (Commit 1, `1b1d325`) с `logger.warning("deprecated_auto_penalty_skipped")`. **(4)** `close_catch_window` переписан (Commit 2, `3b81327`): гейт `now_utc > catch_window_end(yesterday_in_club_tz)` вместо старого `is_within_checkin_window(now_utc)`; housekeeping через `Checkin(status='missed')` + `recompute_pause_status(user_id)` под `lock_for_update(user)`; **0 Penalty / 0 Transaction / 0 deposit-движений**. Cron остался `crontab(minute=5)` (per-habit-beat — отдельная задача). **Никаких миграций.** 87 worker тестов (67 passed, 2 pre-existing failed), 41 backend тестов на Шаг 2, все зелёные. **Known limitations:** (1) `close_catch_window._close_for_habit` бросает `ValueError` на naive datetime — видна только в Celery worker logs (Sentry=no-op), см. §3; (2) cron может запуститься через 0-59 мин после фактической границы (см. §Шаг 3 gate). |

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
| ~~🟡 **Известное несоответствие**: бот и мини-апп показывают РАЗНЫЕ формулировки для `caught_today` (cron `apply_window_expired` vs `apply_catch`)~~ | — | ✅ **Закрыто 2026-08-18 (Шаг 3 серии Pravki-manual-catch-2026-08-18):** `close_catch_window` больше не создаёт `Penalty` с `reason=WINDOW_CLOSED_NO_CATCH` и не шлёт SSE `caught_today`. Cron-only сценарий (юзер не отметился И не был пойман) больше не существует — `caught_today` от worker'а приходит только из ручного catch (`apply_catch` → `process_penalty`). SSE payload `{reason: 'caught_today'}` теперь однозначен. Бот и фронт показывают согласованный текст. Финальное состояние (для истории) — row 244 в HEAD `7c2591b`. |
| 🟡 **Known limitation**: `ValueError` на naive datetime в `close_catch_window._close_for_habit` видна только в Celery worker logs (Sentry = no-op) | `apps/worker/worker/tasks/close_catch_window.py:65-71` | **Контекст:** `close_catch_window._close_for_habit` бросает `ValueError` если передан naive datetime (defensive guard от Шага 2). **Production path** всегда передаёт `datetime.now(tz=timezone.utc)` — tz-aware, ошибка не возникает. Но если в будущем кто-то рефакторит worker и забудет `tz=`, exception поднимется через `asyncio.run(_process())` до Celery-task wrapper. Celery логирует в worker stderr через свой exception handler (`[ERROR] Task ... raised unexpected: ValueError(...)`), task помечается как FAILED. **Sentry = no-op** (`SENTRY_DSN` пуст, см. §1.1 row 8), Prometheus не алёртит. Наблюдаемость = только `docker logs habit-worker | grep ERROR`. **Влияние на юзера:** отсутствует — нарушение будет видно при первом же неудачном cron-вызове. **Не блокирует** текущий deploy. **Когда упадёт:** вероятно никогда (production path всегда tz-aware). **Mitigation после Шага 3:** можно добавить Celery `autoretry_for=(ValueError,)` если понадобится defense — отдельная задача. |
| 🟡 **TODO (cleanup)**: 9 устаревших production-комментариев ссылаются на старую auto-charge модель (`apply_window_expired`/`mark_waived_unable_to_pay`/`WINDOW_CLOSED_NO_CATCH`/`WAIVED_UNABLE_TO_PAY` как активные пути). | `apps/backend/app/core/exceptions.py:127`, `apps/backend/app/repositories/checkin_repository.py:119, 125`, `apps/backend/app/repositories/habit_repository.py:167`, `apps/backend/app/repositories/penalty_repository.py:76, 116`, `apps/backend/app/schemas/__init__.py:167`, `apps/backend/app/services/checkin_service.py:107, 188, 192`, `apps/backend/app/services/penalty_service.py:114, 169, 174-179, 237, 309` (комментарии), `apps/backend/app/api/v1/internal_bot.py:330, 406` | **Контекст:** Эти комментарии оставлены в неактивных code paths (которые не выполняются в новой модели: cron не пишет Penalty, `apply_window_expired`/`mark_waived_unable_to_pay` — no-op). Per `AGENTS.md §12` (точечные правки, не переписывание) — cleanup отложен. **Не влияют на поведение.** **Когда делать:** отдельная задача. **Риск:** через полгода кто-то может решить, что эти комментарии отражают актуальную модель, и сделать рефакторинг в wrong direction. **Mitigation:** в Commit 3 docs уже добавлены явные пометки `⚠️ Устарело с 2026-08-18 (Pravki-manual-catch-2026-08-18 §Шаг 3)` в файлах bot/frontend которые были обновлены; backend-комментарии остались без таких пометок потому что они НЕ в user-facing paths (см. Pravki-manual-catch-step3-2026-08-18.md §Тестовый план для будущего cleanup). |
| 🟡 **Known issue**: nginx на хосте делает **HTML fallback** для `admin.prideclub.fun/admin/assets/admin-*.js` (через 301-redirect на `app.prideclub.fun/admin/assets/...` — возвращает `admin.html` вместо JS bundle). | `nginx` config на хосте (`/etc/nginx/sites-enabled/habit-club`), `proxy_intercept_errors` / `try_files` правила в server-block для admin.prideclub.fun | **Контекст:** Обнаружено 2026-08-21 при деплое Task 1.6 (rsync dist + nginx reload). Прямые запросы `/assets/admin-GzTUgZLx.js` (без `/admin/` prefix) отдают JS корректно — браузер использует именно этот путь. Поэтому **проблема не блокирует юзеров** (admin.html ссылается на `/assets/admin-*.js`, nginx отдаёт JS). **Когда сработает:** при любой интеграции, которая попросит `/admin/assets/...` (например, SSR, скрипты мониторинга, или если admin.html изменится на абсолютный путь). **Pre-existing**, не от деплоя Task 1.6. **Mitigation (когда понадобится):** добавить в server-block `location ^~ /admin/assets/ { alias /usr/share/nginx/html/assets/; try_files $uri =404; }` ДО общего `location /admin/ { try_files ... }` fallback. |

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
