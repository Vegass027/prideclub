# 02 — Архитектура

> Snapshot от 2026-07-22 (обновлено 2026-08-07 после Step 7 — успешный деплой
> Steps 1-6 на проде). Описывает **реально работающую** систему. Концептуальные
> разделы (принципы, потоки) сохранены — они не изменились, только уточнены
> детали. Раздел §9 «Известные проблемы и долги» актуализирован: добавлена
> запись про SSE через Redis Streams (✅ работает на проде с 2026-08-04).

## 1. Принципы

- **Разделение слоёв:** чат (события) ↔ бот (валидация + приём) ↔ backend (бизнес-логика, в одну транзакцию) ↔ worker (фоновые операции через Celery) ↔ Mini App (UI).
- **Асинхронность через очереди** — приём события не блокирует ответ пользователю. Backend кладёт задачи в Celery через `app.services.celery_producer.send_task(...)` (по имени, без импорта worker-модулей).
- **Кэширование горячих данных** (лидерборды, статусы "сегодня", catch rate-limit) в Redis.
- **Stateless-сервисы** для горизонтального масштабирования за балансировщиком.
- **Наблюдаемость** — структурированные JSON-логи (structlog) в backend и worker. Bot логирует plain text.

## 2. Тех-стек

### Frontend — User Mini App
- React 18 + TypeScript
- `@telegram-apps/sdk` (через `window.Telegram.WebApp` в `shared/telegram/tma.ts`)
- TailwindCSS 3
- Vite 6 (сборщик, multi-stage build в Docker)
- React Query 5 (TanStack) — кэширование запросов
- Zustand 5 — глобальный UI-стейт
- React Router v6

### Frontend — Admin Mini App (`apps/frontend/src/admin/`)
- Тот же стек, отдельная секция роутера, отдельный `admin.html` на nginx.
- Хостится на `admin.prideclub.fun`, owner-gate в backend middleware через
  `OWNER_TELEGRAM_ID` (`request.state.telegram_user.id`).

### Backend (`apps/backend/`)
- **Python 3.12** (`FROM python:3.12-slim` во всех Dockerfile'ах)
- FastAPI 0.115 + Uvicorn (2 workers, `--proxy-headers`)
- SQLAlchemy 2.0 (async) + asyncpg 0.30
- Alembic 1.14 — миграции (текущая голова: `009_chat_id_partial_unique`)
- Pydantic 2.10 + pydantic-settings
- redis 5.2 (PyJWT для service-token, aiohttp для исходящих к Telegram API)
- structlog 24 + prometheus-client + sentry-sdk[fastapi] (no-op без DSN)
- pytest 8 / pytest-asyncio / fakeredis / aiosqlite — 161 тест

### Bot Gateway (`apps/bot/`)
- **aiogram 3.30** + aiohttp 3.13 (webhook, не long polling)
- PyJWT 2.10 — генерация service-token для вызовов backend
- structlog 24 (но в `main.py` пока обычный `logging.basicConfig`)
- Один бот на проде, второй admin-бот (`BOT_TOKEN_ADMIN`) — отдельный контур

### Хранение данных
- **PostgreSQL 16** (`postgres:16-alpine` в compose) — основная БД
  (users, habits, memberships, checkins, penalties, transactions, seasons, season_stats,
  suspicious_pairs, offer_versions, user_consents, pricing_rules, bonus_rules,
  season_prize_rules, daily_streak_snapshots).
- **Redis 7** (`redis:7-alpine`, AOF, `--maxmemory 256mb --maxmemory-policy allkeys-lru`)
  — кэш + брокер очередей Celery + today cache + catch rate-limit (Lua).
- **Медиа-кружки** не хранятся долгосрочно — живут в чате Telegram.
- **Аплоады клубов** (фото для Admin Mini App) — Docker volume `club_uploads`,
  расшарен между `habit-backend:/app/static` и `habit-frontend:/usr/share/nginx/html/static`
  (статика отдаётся nginx'ом, без отдельного S3).
- **Резервные копии** — `infra/backup/backup_cron.sh` готов, но **не развёрнут**:
  `aws` CLI не установлен, `S3_*` env-переменных нет, cron-задача не зарегистрирована.

### Очереди и фоновые задачи
- Redis (broker `redis://redis:6379/1`, result backend `redis://redis:6379/2`) + Celery 5.4
- Celery Beat расписание (`apps/worker/worker/celery_app.py`):

| Задача | Cron | Что делает |
|---|---|---|
| `close_catch_window` | `crontab(minute=5)` каждый час | Штрафы без улова (`INSERT ... ON CONFLICT DO NOTHING`) для клубов с закрывшимся окном |
| `expire_bonus_points` | `crontab(hour=3, minute=0)` | Сгорание бонусов старше 90 дней |
| `integrity_check_bonus_transactions` | `crontab(hour=4, minute=0)` | Аудит `bonus_applied=true` без связанной `transactions` |
| `close_season` | `crontab(hour=5, minute=0)` | Распределение призов в конце сезона |

- Воркеры также обрабатывают ad-hoc задачи, положенные backend'ом через
  `app.services.celery_producer.send_task(...)`:

| Task kind | Worker-таска | Откуда вызывается |
|---|---|---|
| `checkin` | `worker.tasks.process_checkin.run` | Backend `POST /internal/checkins/process` (от бота) |
| `penalty` | `worker.tasks.process_penalty.run` | Backend `POST /internal/penalties/catch` (от бота) |
| `payment` | `worker.tasks.process_payment.run` | Backend `POST /internal/payments/confirm` (от бота, **контракт сломан** — см. ниже) |

- Worker запускается с `--pool=solo` (sync-pool, async внутри). На проде — 1 процесс.

### Платежи
- **В MVP — мок на фронте.** `PaymentModal`/`TopUpModal` показывают текст
  "платёжный шлюз не подключён" и `setTimeout(1200)` имитируют оплату.
- Backend и bot код для Telegram Payments **подготовлен** (есть `/internal/payments/confirm`,
  `process_payment` worker-таска, `PaymentService` с `idempotency_key = charge_id`,
  обработчик `successful_payment` в `bot/bot/handlers/payments.py`), но:
  - Бот **не вызывает** `bot.send_invoice(...)` / `bot.create_invoice_link(...)` —
    счёт не выставляется.
  - В `/app/.env` нет `PROVIDER_TOKEN` / `PAYMENT_PROVIDER_TOKEN` /
    `YOOKASSA_*` / `TELEGRAM_STARS_*`.
  - В БД `transactions` — 0 строк (никто не платил).
- **Известный баг:** `internal_payments.py` ожидает `chat_id: int` в payload, а бот
  шлёт `habit_id: str`. Без `chat_id` эндпоинт вернёт `habit_not_found`.

### Инфраструктура
- **Сервер:** Contabo Cloud VPS 4 (4 vCPU / 8 GB / 100 GB SSD), `169.58.52.78`,
  Ubuntu 24.04 LTS. **Не Selectel, не в РФ** — ФЗ-152 под риском для ПДн
  российских пользователей. Миграция на Selectel managed / Yandex Cloud — в плане,
  но не сделана.
- **Docker Compose** (`infra/docker-compose.yml`): 7 сервисов
  (`postgres, redis, backend, bot, worker, frontend, pgweb`).
  Одна bridge-сеть `habit-club_default` (172.18.0.0/16), DNS по именам сервисов.
- **Nginx** — на **хосте** (Ubuntu 24.04, `/etc/nginx/sites-enabled/habit-club`),
  **не** в контейнере. Слушает 80/443, проксирует по доменам.
  Внутри `habit-frontend` — **отдельный** `nginx:1.27-alpine` (multi-stage build),
  отдаёт статику на 80.
- **TLS:** Let's Encrypt, по сертификату на домен, автопродление через cron-certbot.
- **Домены:** `prideclub.fun` (основной, www.), `app.prideclub.fun` (user Mini App),
  `admin.prideclub.fun` (admin Mini App), `api.prideclub.fun` (backend API + bot webhook),
  `db.prideclub.fun` (pgweb, basic auth).
- Подробности по деплою — в [10-deploy.md](10-deploy.md).

### Мониторинг
- **Sentry:** SDK подключён (`sentry-sdk[fastapi]==2.19.2`), но `SENTRY_DSN` пуст
  → no-op. **Sentry не отправляет ошибки.**
- **Prometheus:** `/metrics` отдаёт дефолтные метрики Python + процесса.
  **Кастомных метрик нет** (`habit_*` не заведены).
- **Grafana:** не развёрнута.
- **Алерты в Telegram:** не настроены.
- **Структурированные логи:** structlog + JSONRenderer в backend и worker.
  Bot логирует plain text (в `main.py` `logging.basicConfig`).

## 3. Схема компонентов

```
                ┌──────────────────────┐
                │   Telegram servers    │
                └────┬───────────┬─────┘
                     │ webhook   │ webhook (admin bot)
                     ▼           ▼
              ┌────────────────────────┐
              │   Bot Gateway (aiogram) │  ← приём кружков, команд, успешных оплат
              │   :8080 aiohttp         │     (НЕ выставляет счета)
              └────┬───────────────────┘
                   │ POST /internal/*  (X-Service-Token JWT)
                   ▼
              ┌────────────────────────┐         ┌────────────────────────┐
              │   Backend API (FastAPI) │ ◄────── │  Admin Mini App (React) │
              │   :8000, 2 workers      │         │  admin.prideclub.fun    │
              └──┬─────┬──────────┬────┘         │  owner-gate по           │
                 │     │          │              │  OWNER_TELEGRAM_ID       │
   user requests │     │          │ send_task   └────────────────────────┘
    /api/v1/*    │     │          │ (Celery producer)
                 │     │          ▼
                 │     │     ┌────────────────────────┐
                 │     │     │   Redis 7              │ ← broker + result backend
                 │     │     │   :6379                │   + today cache + rate-limit
                 │     │     └────┬───────────────────┘
                 │     │          │ забирает задачу
                 │     │          ▼
                 │     │     ┌────────────────────────┐
                 │     │     │   Worker (Celery)      │ ← process_checkin,
                 │     │     │   --pool=solo          │   process_penalty,
                 │     │     │   8 tasks + 4 cron     │   process_payment,
                 │     │     └────┬───────────────────┘   apply_catch_bonus,
                 │     │          │                       close_catch_window,
                 │     │          │ asyncpg               close_season,
                 │     │          │                       expire_bonus_points,
                 │     │          │                       integrity_check_*_tx
                 │     │          ▼
                 │     │     ┌────────────────────────┐
                 │     │     │   PostgreSQL 16        │ ← users, habits, memberships,
                 │     │     │   :5432                │     checkins, penalties,
                 │     │     │   volume: pgdata       │     transactions, seasons,
                 │     │     └────────────────────────┘     season_stats, ...
                 │     │
                 │     └────► pgweb (:8081) ← db.prideclub.fun (basic auth)
                 │
                 └────► User Mini App (React)
                       app.prideclub.fun
                       X-Telegram-Init-Data в каждом запросе
```

Все сервисы — в одной compose-сети `habit-club_default` (bridge, 172.18.0.0/16).
Nginx на хосте проксирует на 127.0.0.1 → 5173 (frontend), 8000 (backend),
8080 (bot), 8081 (pgweb). Postgres (5432) и Redis (6379) наружу не отдаются.

## 4. Поток обработки чек-ина

1. Пользователь отправляет кружок в чат клуба (или в топик форума, если
   `checkin_topic_thread_id` задан — см. §10).
2. Telegram отправляет webhook-событие на Bot Gateway
   (`https://api.prideclub.fun/bot/webhook`).
3. Bot Gateway валидирует базовые параметры (chat_id, `message_thread_id`, тип
   сообщения, membership пользователя, попадание в `checkin_window`, медиа —
   `proof_validator.py`) и шлёт `POST /internal/checkins/process` на backend с
   `message_thread_id` в payload.
4. Backend (handler `internal_checkins.py`) кладёт задачу в Celery через
   `send_task("checkin", payload={...})` (`services/celery_producer.py`).
5. Worker (`worker.tasks.process_checkin.run`) забирает задачу:
   - берёт `membership` под `SELECT ... FOR UPDATE`;
   - **топик-фильтр**: если `habit.checkin_topic_thread_id` задан и
     `message_thread_id != checkin_topic_thread_id`, бросает
     `CheckinWrongTopicError` → HTTP 422, без побочных эффектов;
   - валидирует медиа (тип, длительность, **отклоняет forwarded**);
   - пишет строку в `checkins` со статусом `done` (уникальный индекс
     `(membership_id, date)` — один чек-ин в сутки);
   - обновляет кэш "статус сегодня" в Redis (`services/today_cache.py`).
6. Mini App при открытии экрана "Сегодня" читает статус из Redis (быстро),
   при промахе — из БД.

## 5. Поток обработки штрафа

1. **Cron `close_catch_window`** каждый час в `:05` помечает всех, кто не отправил
   доказательство, статусом `missed` и **создаёт `penalties` с
   `reason = 'window_closed_no_catch'`** через `INSERT ... ON CONFLICT (membership_id,
   date, reason) DO NOTHING` (идемпотентно). Это происходит **внутри cron-таски**,
   а не в отдельной `expire_penalties` (такого имени в коде нет — есть
   `expire_bonus_points` для протухания бонусов).
2. Участники клуба видят нарушителей в Mini App (экран "Участники") с кнопкой
   "Спалить".
3. Другой участник нажимает "Спалить" → `POST /internal/penalties/catch` на
   backend. Rate-limit: 10/10s на пользователя (`catch_rate_limiter.py`).
4. Backend кладёт задачу в Celery (`send_task("penalty", payload)`).
5. **Worker `worker.tasks.process_penalty.run`** в **одной транзакции PostgreSQL**
   (`SELECT ... FOR UPDATE` на membership нарушителя):
   - списывает `amount` с `deposit_balance` (или остаток, если депозит меньше);
   - зачисляет в `prize_pool` клуба;
   - создаёт запись в `penalties` с `idempotency_key = penalty:{membership_id}:{date}`;
   - создаёт запись в `transactions` с `balance_after`;
   - если депозит опустился до 0 — `membership.status = paused`.
6. **Отдельная таска `apply_catch_bonus`** (вызывается после `process_penalty`)
   начисляет бонусные поинты ловцу, проверяя `suspicious_pairs` — если пара
   в списке, бонус не начисляется и в лидерборд улов не идёт.

**Окно спаливания = окно чек-ина клуба + 1 час после.** Все нарушители видны
всем одновременно. Защита от сговора — `suspicious_pairs` (см.
[06-data-model.md](06-data-model.md)).

## 6. Масштабируемость

| Этап | Архитектура |
|---|---|
| **MVP (текущее, до ~500 пользователей)** | 1 бот, монолитный FastAPI (2 workers), 1 PostgreSQL 16, 1 Redis 7, 1 worker-процесс (`--pool=solo`). Сервер Contabo 4 vCPU / 8 GB. |
| **Рост (несколько клубов, тысячи пользователей)** | Worker на `prefork` пул, выделить второй worker-процесс для cron-задач, бэкапы на S3, подключить Sentry (DSN) и кастомные Prometheus-метрики. |
| **Масштаб (десятки тысяч)** | Партиционирование `checkins` по дате, отдельный инстанс worker'а для тяжёлых тасок (`close_season`, `process_penalty`), managed PostgreSQL (Selectel), read-replica для лидербордов. |

**Состояния** — в Redis/PostgreSQL, **не в памяти процесса**. На проде worker
использует `--pool=solo` (1 процесс), что формально не "stateless" в смысле
горизонтального масштабирования — увеличение количества worker'ов потребует
переключения на `prefork` или `gevent`. Celery-задачи идемпотентны через
уникальные индексы (`checkins(membership_id, date)`,
`penalties(membership_id, date, reason)`, `transactions.idempotency_key`),
поэтому кратный запуск одной задачи безопасен.

Индексы в PostgreSQL: `users.id` (PK), `habits.id` (PK), `memberships(user_id, habit_id)`,
`checkins(membership_id, date)` UNIQUE, `penalties(membership_id, date, reason)` UNIQUE,
`transactions.idempotency_key` UNIQUE.

## 7. Ключевые решения

| Решение | Обоснование |
|---|---|
| Webhook вместо long polling | Ниже задержка, меньше нагрузка на пике (07:00 утра). |
| Redis-очереди для всех "тяжёлых" операций | Защита от пиков (массовая отправка кружков в одно окно). |
| Bot → Backend API, не к БД напрямую | Бизнес-логика в одном месте, упрощает тестирование и повторное использование (тот же код работает из cron-задач). |
| Кэш статусов "сегодня" в Redis | Снижает нагрузку на БД на 80–90% при правильной инвалидации. |
| **Двухконтурная auth** | `/api/v1/*` — `X-Telegram-Init-Data` (HMAC-SHA256, `WebAppData`), `/internal/*` — `X-Service-Token` (JWT HS256, `aud=backend-api`, `iss=bot/worker`, TTL 60s, leeway 30s). |
| **Owner-gate** для admin | Middleware проверяет `request.state.telegram_user.id == settings.OWNER_TELEGRAM_ID` для всех `/admin/v1/*` эндпоинтов. |
| **Celery `send_task` по имени** | Backend НЕ импортирует worker-модули (`include=[]`), кладёт задачи по строковому имени. Worker их регистрирует через `include=[...]` в `celery_app.py`. Изоляция зависимостей. |
| **Volume `club_uploads` расшарен между backend и frontend** | Фото клубов пишутся backend'ом, отдаются nginx'ом frontend'а. Без отдельного S3 на MVP. |
| **Деньги — `int` копейки** | Никогда `float`/`Decimal` для monetary полей (`deposit_balance`, `penalty_amount`, `prize_pool`, `amount`). |
| **Idempotency через `idempotency_key`** | `transactions.idempotency_key = charge_id` (UNIQUE), `penalties.idempotency_key = penalty:{membership_id}:{date}`. |
| **Worker `--pool=solo`** | На MVP ок (async внутри), но блокирует горизонтальное масштабирование. Замена на `prefork` — при росте. |

## 8. Итоговый стек (актуально на 2026-07-22)

| Слой | Технология | Версия |
|---|---|---|
| Frontend (user Mini App) | React + TypeScript + @telegram-apps/sdk + TailwindCSS 3 + Vite 6 + React Query 5 + Zustand 5 + React Router 6 | — |
| Frontend (admin Mini App) | Тот же стек, отдельный роутер, owner-gate | — |
| Backend API | Python 3.12 + FastAPI 0.115 + SQLAlchemy 2.0 + asyncpg 0.30 + Alembic 1.14 + Pydantic 2.10 + structlog 24 + prometheus-client + sentry-sdk | 161 тест passed |
| Bot Gateway | aiogram 3.30 + aiohttp 3.13 (webhook) + PyJWT 2.10 | — |
| Worker | Celery 5.4 + asyncpg + structlog, `--pool=solo`, 8 tasks + 4 cron | 34 тест passed |
| Очереди | Redis 7 (broker + result backend + cache + rate-limit) | — |
| БД | PostgreSQL 16, 9 миграций (000 → 009) | 16 таблиц |
| Админка БД | sosedoff/pgweb (basic auth, `db.prideclub.fun`) | — |
| Reverse proxy | nginx на хосте (Ubuntu) + nginx 1.27 внутри frontend-контейнера | — |
| TLS | Let's Encrypt, по сертификату на домен | 89 дней до продления |
| Платежи | Мок на фронте; backend/bot код подготовлен, бот не выставляет счета | — |
| Хостинг | Contabo VPS 4 (Германия, **НЕ РФ**) | — |
| Мониторинг | Sentry SDK без DSN (no-op), Prometheus /metrics без кастомных метрик, Grafana отсутствует, structlog JSON в backend/worker | — |
| CI/CD | GitHub Actions (lint + test) + Dependabot | — |
| Резервные копии | `backup_cron.sh` готов, **не развёрнут** (нет aws CLI, нет S3 env) | — |

## 9. Известные проблемы и долги (snapshot 2026-07-23, обновлено 2026-08-09)

> Обновлено 2026-07-23 после закрытия задачи topic-scoped check-in.
> Свежие правки 2026-08-09 — см. `docs/AGENT_BOOTSTRAP.md §9` (там более полный
> список с фактическим состоянием после Pravki-subscribe-and-join deploy'а).

| Что | Где | Статус |
|---|---|---|
| 🟡 Платежи = мок на уровне Telegram API (НЕ на уровне backend) | `bot/handlers/payments.py` не вызывает `send_invoice` | Backend `POST /api/v1/payments/subscribe` работает (commit `b98cab0`, 2026-08-09). Реальный Telegram Payments — отдельная задача, см. `Pravki-subscribe-and-join.md §6` |
| ⚠️ Docker overlay-конфликт при `docker compose build frontend` | `infra/docker-compose.yml` | Workaround применён (commit `4a390e1`): `image: nginx:1.27-alpine` + volume mount. Подробности и диагностика — `docs/10-deploy.md` §9.1 |
| ⚠️ Alembic upgrade через compose не выполняет ALTER TYPE ADD VALUE | `apps/backend/alembic/versions/015_*.py` | Workaround через ручной `psql` + `UPDATE alembic_version`. Подробности — `docs/10-deploy.md` §9.2 |
| ⚠️ Контракт `chat_id` vs `habit_id` | `internal_payments.py` ↔ `bot/handlers/payments.py` | Сломано, требует фикса перед включением платежей (или удалить мёртвый код — см. `Pravki-subscribe-and-join.md §0`) |
| ⚠️ Контракт `chat_id` vs `habit_id` в `apps/backend/app/api/v1/internal_payments.py` ожидает `chat_id: int`, бот шлёт `habit_id: str`. 422 без починки | | Сломано — мёртвый код (бот не вызывает). Удалить или переименовать параметр — отдельная задача |
| ⚠️ Sentry = no-op | `SENTRY_DSN` пуст в `.env` | Без изменений |
| ❌ Бэкапы не развёрнуты | `infra/backup/`, `.env` | Сценарий в плане, не выполнен |
| Бот логирует plain text | `bot/main.py:25` | Не structlog, как в backend/worker (warning не блокирует) |
| Nginx на хосте дублирует фронт | `/etc/nginx/sites-enabled/habit-club` + `habit-frontend` (nginx 1.27) | Двухслойный прокси; не баг, но усложняет деплой (см. §12 ниже) |
| Server в Германии | `169.58.52.78` (Contabo) | ФЗ-152 под риском; миграция в Selectel — план |
| `habits` = 0 в БД при наличии аплоадов | `uploads/club_photos/` (9 файлов) vs `SELECT count(*) FROM habits` | **Исправлено** — клубы созданы через admin API; 2 клуба в проде (`Планка`, `Пробежка`) |
| `SERVICE_TOKEN_TTL_SECONDS=60`, `INIT_DATA_MAX_AGE_SECONDS=86400` | `.env` | Стандартно, см. `core/constants.py` |
| Admin Mini App `OWNER_TELEGRAM_ID=0` по умолчанию | `docker-compose.yml` | Без явного ID в env owner-gate пускает никого — правильно. |

### 9.1. Закрытые проблемы (snapshot 2026-07-23)

| Что | Где | Фикс |
|---|---|---|
| Bot webhook SSL error → pending_update_count растёт | `bot/main.py`, `infra/.env` | `WEBHOOK_BASE_URL=https://169.58.52.78` → `https://api.prideclub.fun`; fail-fast в проде через `_validate_webhook_url` |
| Worker `NameError: CheckinWrongTopicError is not defined` → молчаливый retry-loop | `apps/worker/worker/tasks/process_checkin.py:99` | Добавлен импорт в `from app.core.exceptions import ...` |
| Mini App `status=pending` не обновляется после кружка | топик-фильтр + worker | `forward_to_thread_id` корректно резолвится через `message_thread_id` от Telegram |
| PATCH `/admin/v1/habits/{id}` не сохранял price_month | `apps/frontend/src/admin/pages/HabitEditForm.tsx` | Добавлен `price_month` и `penalty_amount` в payload + helper `rubToKopecks`; типы `AdminHabitUpdatePayload` расширены |
| Backend ForwardRef('Response') "class not fully defined" | `apps/backend/app/core/middleware.py` | Убран `from __future__ import annotations` (PEP 563 + starlette.Response = нерезолвимый forward ref) |

### 9.2. Закрытые проблемы (snapshot 2026-08-04 — post-mortem: «бот молчит, чек-ины не доходят»)

| Что | Где | Фикс |
|---|---|---|
| `WEBHOOK_BASE_URL` и `WEBAPP_URL` указывали на сырой IP `169.58.52.78` в `/app/infra/.env` (а не на домен) → Telegram не мог доставить апдейты (SSL handshake fail) → `pending_update_count: 2` копилось бесконечно | `infra/docker-compose.yml` (через `${WEBHOOK_BASE_URL}` подстановку из `/app/infra/.env`) + `bot/main.py:111` | (1) Поправил `/app/infra/.env` на проде: `WEBHOOK_BASE_URL=https://api.prideclub.fun`, `WEBAPP_URL=https://app.prideclub.fun`; (2) Добавил `ENVIRONMENT: ${ENVIRONMENT:-production}` в `x-bot-env` anchor — теперь `bot/main.py:_validate_webhook_url` срабатывает на проде; (3) Использовал `${WEBHOOK_BASE_URL:?must be set in prod}` и `${WEBAPP_URL:?must be set in prod}` — пустое значение теперь роняет `docker compose up` ещё до старта контейнера; (4) После правки — `docker compose up -d bot`, Telegram доставил 2 зависших апдейта, чек-ины пошли |
| Worker `CheckinService.__init__() missing 1 required positional argument: 'penalty_repo'` → молчаливый `ok=False, err=...` (Task succeeded per Celery, но в БД ничего) | `apps/worker/worker/tasks/process_checkin.py:62-68` | Добавлен `penalty_repo=PenaltyRepository(session)` (как в `apps/backend/app/api/v1/habits.py:42-49`). Причина бага: в `CheckinService.__init__` добавили `penalty_repo` для `get_today_status` (для `TodayStats.penalties_count/total`), но забыли обновить worker. **Скрытый анти-паттерн:** `process_checkin._process()` имеет `except Exception` catch-all, который глотает любую ошибку инстанцирования и возвращает `{"ok": False, "err": "..."}` — это маскирует баги в DI. Не блокирует (это вне scope этого фикса), но в backlog |

**Урок для будущих агентов:**

1. На сервере ДВА `.env`-файла, не один:
   - `/app/.env` — монтируется в backend-контейнер (читается приложением).
   - `/app/infra/.env` — читается `docker-compose` для `${VAR}` подстановки.
   - **`WEBHOOK_BASE_URL`, `WEBAPP_URL`** — переменные для `docker-compose`, живут в `/app/infra/.env`. Правка `/app/.env` не помогает.
2. После правки `WEBHOOK_BASE_URL`/`WEBAPP_URL` — перезапустить **только** `habit-bot`: `ssh privichki-prod 'cd /app/infra && docker compose up -d bot'`. **Не делать `docker compose down`** — это гасит весь стек.
3. При добавлении новых обязательных параметров в DI-конструктор сервиса (`CheckinService`, `PenaltyService`, и т.д.) — синхронно обновить **все** call-сайты: backend API DI (`apps/backend/app/api/v1/*.py`), worker таски (`apps/worker/worker/tasks/*.py`), admin endpoint DI. Перед merge — `rg "ServiceName\\("` по всему проекту.
4. Worker-таски имеют `except Exception` catch-all, который **маскирует баги DI** как `ok=False`. Если в логах видишь `worker_checkin_failed` с `err='CheckinService.__init__() ...'`, НЕ игнорируй — это серьёзный баг.

### 9.3. SSE через Redis Streams — текущая ситуация (snapshot 2026-08-07)

> Полный план — `sse+redis.md`. Шаги 1-6 (backend SSE endpoint, middleware bypass,
> worker event_publisher, XREAD pipeline + async-Redis singleton, nginx SSE
> блок, frontend `useTodayStream`) **задеплоены и работают на проде с 2026-08-04**.
> Покрыты 100 тестами (67 backend + 22 worker + 11 frontend vitest).

| Что | Где | Статус |
|---|---|---|
| **Steps 1-4: backend SSE + middleware bypass + worker event_publisher + XREAD pipeline** | `apps/backend/app/services/sse/{sse_token,sse_formatter,redis_stream_bus,connection_limiter}.py`, `apps/backend/app/api/v1/events.py`, `apps/backend/app/core/middleware.py` (SSE_AUTH_BYPASS_PATHS), `apps/backend/app/db/redis_async.py` (async-singleton), `apps/worker/worker/services/event_publisher.py`, `apps/worker/worker/tasks/process_checkin.py` (Guard 1 + Guard 2) | ✅ **ЗАДЕПЛОЕНЫ** на проде `2026-08-04` (merge commit `0c9a7b8`, deploy через `docker compose build backend worker --no-cache && docker compose up -d backend worker`). Образ `habit-backend`: `63e354d14c7820e4831167954f224be419c49b4b6afc7f28912ec7e6f9aff82f`. Smoke-test: `POST /api/v1/events/stream/token` (без initData) → 401 `{"code":"missing_init_data"}` (НЕ 503 `sse_not_configured` — значит `SSE_TOKEN_SECRET` дошёл до backend). Реальный чек-ин через Mini App → `worker sse_publish_ok event=checkin.accepted` (ровно 1 на чек-ин, Guard 2 не задвоил). Redis stream `sse:user:7295309649:<habit_id>` создан, XLEN=1, idempotency key `sse_published:checkin:7af92214-...:2026-08-04` TTL=24 ч. |
| **Step 5: nginx `location = /api/v1/events/stream`** | `infra/nginx/nginx.prideclub.conf:97-112`, `nginx.prod.conf:79-99`, `infra/nginx/habit-club-sse.conf.snippet`, `/etc/nginx/sites-enabled/habit-club` (на хосте) | ✅ Применено на проде `2026-08-04` (commit `900ef4f`). `nginx -t` ОК. Debug-тест с `return 418` подтвердил exact-match (блок срабатывает только для точного `/api/v1/events/stream`, `POST /events/stream/token` остаётся под общим `/api/` блоком). Бэкап `/var/backups/nginx/habit-club.bak.20260804_1823`. |
| **Step 6: frontend `useTodayStream` + `streamController` + `sseToken`** | `apps/frontend/src/shared/api/sseToken.ts`, `apps/frontend/src/shared/hooks/streamController.ts` (pure-function с 7-param DI), `apps/frontend/src/shared/hooks/useTodayStream.ts` (тонкая обёртка), `apps/frontend/src/pages/Today/TodayPage.tsx` | ✅ **ЗАДЕПЛОЕН** на проде `2026-08-04` через двухслойный nginx-метод (`docs/02-architecture.md §13`): `docker run node:20-alpine + npm ci + npm run build` + `docker cp dist` + `nginx -s reload`. Commits `5d8c6e6` + `d30832a`. Mount-invalidate в `TodayPage` удалён (`d30832a`): useToday со `staleTime: 30_000` сам управляет stale-инвалидацией, SSE даёт real-time freshness. **Pure-function controller** (вместо inline EventSource в хуке) — DI через 7 параметров (`habitId, queryClient, createEventSource, requestToken, setTimeoutFn, clearTimeoutFn, onError, streamBaseUrl`), тестируется без React-renderer. 11 vitest unit покрывают: initial open URL shape, checkin.accepted → setQueryData, checkin.rejected → onError, onerror → close + backoff + новый EventSource с свежим токеном, lastEventId в reconnect URL, stop() отменяет pending backoff, requestToken throws → backoff retry, start() идемпотентность. **One `as unknown as` каст** в одной DI-точке (EventSource → StreamEventSourceCtor — TypeScript не делает covariance на constructor return types), помечен комментарием с обоснованием. |
| **Инфра-фиксы для деплоя** | `apps/frontend/.dockerignore` (commit `0ceb647`), `infra/docker-compose.yml` — `x-backend-env` (commit `4d821d6`): `SSE_TOKEN_SECRET: ${SSE_TOKEN_SECRET}` и `SSE_TOKEN_TTL_SECONDS: ${SSE_TOKEN_TTL_SECONDS:-60}` | ✅ **ЗАДЕПЛОЕНЫ**. `.dockerignore` исключает `node_modules` из build context frontend (без него `docker compose build frontend` падает с `cannot replace to directory .../node_modules/@tanstack/react-query with file`). `SSE_TOKEN_SECRET` подключён через compose-интерполяцию из `/app/infra/.env` (НЕ `/app/.env` — `/app/.env` **не монтируется** в контейнер, было неверное предположение в первоначальной инструкции; реальный паттерн проводки — `${VAR}` из `/app/infra/.env` через `x-backend-env`). |
| **SSE_TOKEN_SECRET** | `/app/infra/.env`: `SSE_TOKEN_SECRET=<76 chars urlsafe>`, `SSE_TOKEN_TTL_SECONDS=60`. Backup `/app/infra/.env.bak.20260804_202231`. | ✅ Работает на проде. Генерируется `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Длина 76 символов (48 байт base64-urlsafe). |
| **Что известно не покрыто локально без Docker** | тесты `test_admin_habits_api.py::TestAdminHabitEndpoints` (×9) и `test_migrations.py::test_alembic_round_trip_on_real_postgres` | ⚠️ Физически требуют настоящего Redis/Postgres через `docker-compose exec backend pytest`. Проходят только через CI или на проде. Не блокер — SSE-код покрыт 89 тестами через fakeredis/aiosqlite, всё зелёное. |
| **Connection limiter edge-case** | `services/sse/connection_limiter.py` | ⚠️ `sse:conn:{user_id}` expire'ируется через TTL=180с если `release()` не вызвался. В реальном кейсе: TTL=180с после закрытия коннекта — окно для `try_acquire` на 6-й коннект (лимит 5). Для MVP приемлемо (страховка от `kill -9`); в v2 — добавить `logger.debug("sse_release_ok user=... count=...")` для верификации cleanup'а через `grep sse_release`. |

---

## 10. Topic-scoped чек-ины и уведомления (миграции 010, 011)

> Реализовано 2026-07-23. Детальная схема — `docs/06-data-model.md` §6.

### 10.1. Проблема

Клубы живут в **супергруппах с включённым режимом форумов (топиков)**. Без
фильтра участники могли отправлять кружки в любой топик, а бот принимал их все —
это нарушает контракт «чек-ин только в одном месте». Также не было удобного
места для публикации уведомлений о ловле — они шли в общий чат и смешивались
с обсуждением.

### 10.2. Решение

Каждый клуб привязывается к **трём топикам** в одной супергруппе:

1. **Топик чек-инов** (`habits.checkin_topic_thread_id`). Бот принимает только
   сообщения из этого топика. Сообщения из General или других топиков → код
   `not_checkin_topic` (HTTP 422), без записи в `checkins`.
2. **Топик уведомлений** (`habits.notifications_topic_thread_id`). Сюда бот
   публикует события ловли (`👨🏽‍🦰 X словил(а) 👨🏽‍🦰 Y, 💸 N ₽`) и штрафов за
   пропуск (`⏰ Окно закрыто`). Топик обязателен для админа, но
   технически может быть NULL (тогда уведомления идут в General).
3. **Топик чата клуба** (`habits.chat_topic_thread_id`). Кнопка «💬 Перейти в чат»
   в User Mini App открывает именно этот топик — отдельное место для переписки
   участников, не пересекающееся с чек-инами и уведомлениями.

### 10.3. Связь с антифродом

Topic-scoped фильтр дополняет существующие антифрод-механизмы (`docs/06-data-model.md` §4):

- Уникальный индекс `(membership_id, date)` по-прежнему гарантирует один чек-ин в день.
- Forwarded-сообщения по-прежнему отклоняются (`proof_validator.py`).
- Топик-фильтр не зависит от типа медиа — он работает по `message_thread_id`.

### 10.4. Где меняется код

| Слой | Файл | Что |
|---|---|---|
| Миграции | `apps/backend/alembic/versions/010_habit_topics.py`, `011_habit_chat_topic.py` | Колонки + partial btree |
| Модель | `apps/backend/app/models/habit.py` | Поля `checkin_topic_thread_id`, `notifications_topic_thread_id`, `chat_topic_thread_id` |
| Парсер | `apps/backend/app/core/telegram_links.py` | Нормализация `chat_id` из короткой формы ссылки в Bot API-форму |
| Репозиторий | `apps/backend/app/repositories/habit_repository.py` | `get_by_chat_and_thread()` — для дедупликации топиков |
| Сервис | `apps/backend/app/services/habit_service.py` | Валидация трёх топиков на create/update, `code=habit_topics_must_differ`, `code=habit_topic_duplicate` |
| Сервис | `apps/backend/app/services/checkin_service.py` | Фильтр `message_thread_id`, `code=not_checkin_topic` |
| Сервис | `apps/backend/app/services/notification_service.py` | Публикация в топик уведомлений через прямой Bot API |
| Worker | `apps/worker/worker/tasks/process_checkin.py` | Прокидывает `message_thread_id` в сервис |
| Worker | `apps/worker/worker/tasks/process_penalty.py` | После `commit()` шлёт `notification_service.notify_catch(...)` |
| Worker | `apps/worker/worker/tasks/close_catch_window.py` | После `commit()` шлёт `notification_service.notify_window_closed(...)` |
| Bot | `apps/bot/bot/handlers/checkin.py` | Прокидывает `message.message_thread_id` в payload |
| Admin Mini App | `apps/frontend/src/admin/pages/HabitCreatePage.tsx`, `HabitEditForm.tsx` | Три поля для топик-ссылок + валидация |
| User Mini App | `apps/frontend/src/shared/telegram/topicLink.ts` | `buildTopicLink(chat_id, thread_id)` отбрасывает `-100` префикс |
| User Mini App | `apps/frontend/src/pages/MyHabits/MyHabitsPage.tsx` | Кнопки «Сделать чек-ин» / «Перейти в чат» на карточках |
| User Mini App | `apps/frontend/src/pages/Today/TodayPage.tsx` | Кнопки + секция «Клуб в Telegram» |

### 10.5. UX-инварианты

- Доменная ссылка формируется как `https://t.me/c/<short_id>/<thread_id>`, не
  `https://t.me/c/-100<short_id>/<thread_id>`. Telegram не принимает Bot API-форму
  в URL, только короткую.
- Кнопка «🎬 Сделать чек-ин» появляется только если `checkin_topic_thread_id` задан
  и `chat_id != 0`. Иначе — fallback на «Открыть чат клуба».
- Кнопка «💬 Перейти в чат» появляется только если `chat_topic_thread_id` задан.
- Секция «Клуб в Telegram»: если `membership.status === "active"` — disabled-кнопка
  «❤️ Вы состоите в клубе»; иначе — CTA «👋 Присоединиться к клубу».

---

## 11. Multi-proof_types — клуб принимает 1-3 типа чек-ина (миграция 012)

> Реализовано 2026-07-23. Детальная схема — `docs/06-data-model.md` §3
> (миграция 012) и §10 (anti-fraud rules).

### 11.1. Проблема

Раньше у клуба был ровно один `proof_type` (`video_note` | `photo` | `text`).
Владелец не мог разрешить «кружок ИЛИ фото» — только одно из трёх.

### 11.2. Решение

- БД: `habits.proof_types JSONB NOT NULL` — массив из 1..3 строк ∈ enum.
- `habits.proof_type` остаётся как **alias** `proof_types[0]` для обратной
  совместимости со старыми клиентами.
- Backend API `AdminHabitCreateRequest` / `AdminHabitUpdateRequest` принимают
  `proof_types` (или устаревший `proof_type`, конвертируется в массив).
- CheckinService проверяет `proof.proof_type.value in habit.proof_types`.
- Admin Mini App: `CheckboxGroup` вместо `RadioGroup` (1..3 опции, минимум 1
  обязателен).

### 11.3. Обратная совместимость

Миграция 012 бэкфиллит существующие строки:

```sql
UPDATE habits SET proof_types = jsonb_build_array(proof_type::text);
```

Существующие клубы (2 в проде) сохраняют `proof_types = ["video_note"]`.
Чек-ины работают как раньше.

### 11.4. Что НЕ менялось

- Frontend User Mini App (PR плана №8 — отдельный коммит).
- Bot pre-filter (PR плана №9 — отдельный коммит).
- Миграция `012_proof_types.py` — append-only (правило §3.2
  AGENT_BOOTSTRAP.md).

---

## 12. Force-update финансов (owner-only escape hatch)

> Реализовано 2026-07-23. См. также `apps/backend/app/api/admin/v1/habits.py`
> (`PATCH /admin/v1/habits/{id}/force-financials`) и `HabitService.force_update_financials`.

### 12.1. Зачем

По умолчанию `price_month` и `penalty_amount` были заморожены после первого
вступления в клуб (финансовая целостность, ФЗ-152). Owner не мог поднять цену
даже при объявлении участникам за неделю. Типичный use case — поднять цену с
нового месяца.

### 12.2. Решение

Заморозка СНЯТА. Middleware `/admin/v1/*` уже гейтит доступ только owner'у —
если вызов прошёл, это owner, доверяем.

`PATCH /admin/v1/habits/{id}` теперь принимает `price_month` / `penalty_amount`
без проверки `active_members_count`. Endpoint `/force-financials` оставлен
для targeted-обновления только финансов (без пересохранения остальных полей).

### 12.3. Семантика (важно для compliance)

**Меняется**:
- `habits.price_month` — применяется к новым подпискам.
- `habits.penalty_amount` — применяется к будущим штрафам.

**НЕ меняется** (никогда):
- `users.deposit_balance` участников.
- `memberships.subscription_until` (оплаченный период остаётся).
- `memberships.auto_renew_enabled`.
- `memberships.status` (никого не выгоняет).

Уже оплаченные подписки продолжают действовать до конца оплаченного периода
по старой цене. Новые подписки — по новой.

### 12.4. Audit

Каждое force-update логируется WARN с полным контекстом:
`admin_id`, `habit_id`, `old_price_month`, `new_price_month`, etc.

---

## 13. Деплой фронтенда — двухслойный nginx

> ⚠️ Усложнение для деплоя. Стандартный `docker compose build frontend`
> не обновляет dist в nginx-контейнере (см. `infra/deploy.sh` step 4).
>
> **Решение для разработчика**: после rsync `apps/frontend/` в `/app/apps/frontend/`
> на сервере:
>
> ```bash
> docker run --rm -v /app/apps/frontend:/app -w /app node:20-alpine \
>   sh -c "npm ci --silent && npm run build"
> docker cp /app/apps/frontend/dist/. habit-frontend:/usr/share/nginx/html/
> docker exec habit-frontend nginx -s reload
> ```
>
> Хостовая `/usr/share/nginx/html/` и контейнерная — **разные папки**,
> потому что nginx-образ сам по себе содержит HTML. Build в образе работает
> только при изменении `Dockerfile`, не исходников.

---

## 14. Bot pre-filter по allowed_proof_types и дубликату (PR №9)

### 14.1. Проблема

Backend `POST /internal/checkins/process` сразу возвращает
`{ok: True, task_id: ...}` после `send_task()` в Celery — не дожидаясь
результата worker'а. Worker отвергает задачу асинхронно (например,
`code: wrong_type` или `checkin_already_exists`), но **бот уже ответил
юзеру «Принято, молодец»**. Юзер видит «✅ Принято» в топике, а в БД чек-ин
не создан → UX-баг.

### 14.2. Решение

Бот **ПЕРЕД** отправкой в backend запрашивает
`GET /internal/bot/habit_state?chat_id=...&user_id=...` (новый internal
endpoint) и:
- если `proof_types` не содержит тип сообщения → отвечает понятным
  сообщением «в этом клубе принимается только X» и НЕ шлёт в backend;
- если `already_checked_in=true` → отвечает «ты уже отметился сегодня» и
  НЕ шлёт в backend;
- если `state` недоступен (сеть/Redis) → fallback на старый путь;
- если `habit` не найден → молча (как `habit_not_found`).

### 14.3. Endpoint

`GET /internal/bot/habit_state?chat_id=...&user_id=...`
(`apps/backend/app/api/v1/internal_bot.py`):

```python
class HabitStateResponse(BaseModel):
    found: bool
    habit_id: str | None = None
    proof_types: list[str] = []
    checkin_topic_thread_id: int | None = None
    already_checked_in: bool = False
    checked_in_at: datetime | None = None
```

Auth: `X-Service-Token` (тот же JWT, что у остальных `/internal/*`).

`already_checked_in` вычисляется по `habit.club_date(now_utc)` — клуб-local
today, не UTC. Используется `CheckinRepository.get_for_date(membership_id,
club_date)`.

### 14.4. Где меняется код

- `apps/backend/app/api/v1/internal_bot.py` — новый endpoint + Pydantic
  модель.
- `apps/backend/tests/test_internal_habit_state.py` — 7 unit-тестов.
- `apps/bot/bot/services/api_client.py` — `get_habit_state(chat_id, user_id)`
  + поддержка `params` в `get()`.
- `apps/bot/bot/handlers/checkin.py` — новый `_prefilter()` +
  `_detect_proof_type()`. Вызывается ДО `backend.post()`.
- `apps/bot/bot/handlers/checkin_texts.py` — `reject_wrong_type_single()`,
  `reject_wrong_type_multi()`, `REJECT_UNSUPPORTED_TYPE`,
  `REJECT_ALREADY_CHECKED_IN`.
- `apps/bot/tests/test_checkin_handler.py` — 6 новых pre-filter тестов.

### 14.5. UX-инварианты

- Если у юзера уже есть чек-ин за сегодня → **любое** следующее сообщение
  в топик чек-инов получает «уже отметился» (не пытаемся отвечать про тип).
- Если тип не поддерживается → сообщение с эмодзи и списком разрешённых.
- Если сообщение пришло в чат, к которому бот не привязан → молчим.

