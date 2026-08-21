# 03 — Структура проекта

> Snapshot от 2026-07-22. Актуальная раскладка монорепо.

Монорепо с общими типами между backend и frontend. При росте команды можно разнести
на отдельные репозитории без изменения внутренней структуры сервисов.

## 1. Структура репозитория

```
habit-club/
├── apps/
│   ├── frontend/         # Telegram Mini App (React + TS)
│   ├── backend/          # Backend API (FastAPI)
│   ├── bot/              # Bot Gateway (aiogram)
│   └── worker/           # Celery workers
├── packages/
│   └── shared/           # Общие типы/схемы (Pydantic ↔ TS)
├── infra/
│   ├── docker/           # Dockerfile для каждого сервиса
│   ├── nginx/            # Конфигурация reverse proxy
│   ├── backup/           # backup_cron.sh + rotate_backups.py
│   └── docker-compose.yml
├── .github/
│   ├── workflows/        # CI/CD для каждого сервиса
│   └── dependabot.yml
├── docs/                 # Эта документация
├── .env.example
├── Makefile
├── README.md
└── AGENTS.md
```

## 2. Frontend — Mini App (apps/frontend)

```
frontend/
├── public/
├── src/
│   ├── app/
│   │   ├── App.tsx
│   │   ├── providers/    # Theme, TelegramSDK, QueryClient
│   │   └── router.tsx
│   │
│   ├── pages/            # 1 директория = 1 экран (user Mini App)
│   │   ├── Onboarding/
│   │   ├── Marketplace/
│   │   ├── MyHabits/
│   │   ├── Today/
│   │   ├── Members/
│   │   ├── Leaderboard/        # внутри клуба
│   │   ├── GlobalLeaderboard/  # рейтинг по всем клубам юзера
│   │   └── Profile/
│   │
│   ├── admin/            # Admin Mini App (отдельный роутер, отдельный nginx endpoint)
│   │   ├── api/          # adminHabitsApi
│   │   ├── components/   # AdminHabitCard
│   │   ├── hooks.ts
│   │   └── pages/        # HabitsListPage, HabitCreatePage, HabitEditForm
│   │
│   ├── widgets/          # Составные блоки (HabitCard, MemberRow, DepositWidget)
│   │
│   ├── shared/
│   │   ├── ui/           # Атомарные (Button, BottomNav, HabitNav, Avatar, Tabs, Modal, Skeleton, …)
│   │   ├── api/          # axios-клиент + типизированные endpoint'ы
│   │   ├── telegram/     # Обёртка @telegram-apps/sdk (initData, mainButton, haptics)
│   │   ├── hooks/        # useQuery / useMutation обёртки
│   │   ├── types/        # TS-типы (синхронизированы с backend)
│   │   └── utils/        # formatKopecks и т.п.
│   │
│   ├── assets/           # Иконки, изображения
│   ├── docs/             # apps/frontend/docs/STATUS.md (специфика фронта)
│   └── main.tsx
│
├── package.json          # React 18, Vite 6, Tailwind 3, React Query 5, Zustand 5
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.js
```

**Ключевые решения:**
- **Vite 6** — быстрый dev-сервер, multi-stage Docker build (node:20-alpine → nginx:1.27-alpine).
- **React Query 5** — кэширование, повторные запросы, синхронизация без перерисовок.
- **Zustand 5** — лёгкий глобальный UI-стейт (выбранный месяц, открытые модалки).
- **`shared/telegram/`** — изоляция вызовов Telegram SDK от остального кода.
- **Admin Mini App** живёт в `src/admin/`, маршруты регистрируются отдельным блоком
  в `router.tsx`, отдаётся nginx'ом как `admin.html` на `admin.prideclub.fun`.

## 3. Backend API (apps/backend)

```
backend/
├── app/
│   ├── main.py                    # Точка входа FastAPI, lifespan, CORS, middleware
│   ├── core/
│   │   ├── config.py              # Pydantic Settings (переменные окружения)
│   │   ├── security.py            # validate_init_data, validate_service_token, JWT
│   │   ├── middleware.py          # auth_middleware: initData / service_token / owner-gate
│   │   ├── logging.py             # Структурированное логирование (structlog)
│   │   ├── exceptions.py          # Доменные исключения + глобальный обработчик
│   │   └── constants.py           # Enums, конфиги (PenaltyConfig, MembershipStatus)
│   │
│   ├── api/
│   │   ├── v1/                    # user-контур (/api/v1/*, initData)
│   │   │   ├── users.py
│   │   │   ├── habits.py
│   │   │   ├── memberships.py
│   │   │   ├── checkins.py
│   │   │   ├── penalties.py
│   │   │   ├── leaderboard.py
│   │   │   ├── balance.py
│   │   │   ├── internal_bot.py
│   │   │   ├── internal_checkins.py
│   │   │   ├── internal_penalties.py
│   │   │   ├── internal_payments.py
│   │   │   ├── admin_suspicious_pairs.py
│   │   │   └── health.py          # /health, /ready, /metrics
│   │   │
│   │   └── admin/                 # admin-контур (/admin/v1/*, initData + OWNER_TELEGRAM_ID)
│   │       └── v1/
│   │           ├── habits.py      # CRUD, activate, archive, restore, preview/refresh chat
│   │           └── uploads.py     # POST /admin/v1/uploads (фото клубов)
│   │
│   ├── models/                    # SQLAlchemy модели
│   ├── schemas/                   # Pydantic-схемы запросов/ответов (включая AdminHabitOut)
│   ├── services/                  # Бизнес-логика
│   │   ├── checkin_service.py
│   │   ├── penalty_service.py
│   │   ├── habit_service.py       # в т.ч. admin: create, list_including_archived
│   │   ├── membership_service.py
│   │   ├── payment_service.py     # prepare/deprecated: мок на фронте, код готов
│   │   ├── bonus_service.py
│   │   ├── season_service.py
│   │   ├── suspicious_pairs_service.py
│   │   ├── proof_validator.py
│   │   ├── today_cache.py
│   │   ├── catch_rate_limiter.py  # Lua: 10 catches / 10s
│   │   ├── http_rate_limiter.py
│   │   └── celery_producer.py     # backend send_task(...) → worker по имени
│   │
│   ├── repositories/              # Слой доступа к данным
│   ├── db/
│   │   ├── session.py             # Подключение к PostgreSQL (async)
│   │   └── redis.py               # Подключение к Redis
│   │
│   └── tasks/                     # (зарезервировано)
│
├── alembic/
│   ├── versions/                  # 000_extensions → 009_chat_id_partial_unique
│   └── env.py
├── tests/                         # 161 тест (pytest + fakeredis + aiosqlite)
├── scripts/                       # register_webhook.py, seed_dev_data.py
├── requirements.txt               # fastapi 0.115.5, sqlalchemy 2.0.36, asyncpg 0.30
├── alembic.ini
└── pyproject.toml
```

**Ключевые решения:**
- **Слоистая архитектура:** `api → services → repositories → models`. Каждый слой знает
  только о слое ниже.
- **Async SQLAlchemy 2.0** для неблокирующей работы с БД.
- **Alembic** для версионирования схемы.
- **Pydantic 2.10** как единый источник правды для валидации.
- **Celery `send_task` по имени**: backend НЕ импортирует worker-модули (`include=[]`),
  кладёт задачи по строковому имени через `app.services.celery_producer`.
- **Двухконтурный API**: `/api/v1/*` (initData) + `/internal/*` (service_token JWT) +
  `/admin/v1/*` (initData + owner-gate по `OWNER_TELEGRAM_ID`).

## 4. Bot Gateway (apps/bot)

```
bot/
├── bot/
│   ├── main.py                    # aiohttp webhook-сервер на :8080
│   ├── handlers/
│   │   ├── start.py               # /start, приветствие, открытие Mini App
│   │   ├── checkin.py             # Обработка video_note/photo в чатах клубов → /internal/checkins/process
│   │   ├── payments.py            # pre_checkout_query + successful_payment → /internal/payments/confirm
│   │   ├── chat_member.py         # Бот добавлен/удалён из чата клуба (Redis-кэш available_chats)
│   │   └── admin.py               # Команды администратора
│   │
│   ├── middlewares/
│   │   ├── rate_limit.py
│   │   └── __init__.py
│   │
│   ├── services/                  # (зарезервировано)
│   ├── logging_setup.py
│   └── config.py                  # pydantic-settings: BOT_TOKEN, WEBHOOK_BASE_URL, SERVICE_SECRET
│
├── requirements.txt               # aiogram 3.30, aiohttp 3.13, PyJWT 2.10, structlog 24
└── (Dockerfile — в infra/docker/bot.Dockerfile)
```

**Ключевые решения:**
- **Webhook-режим на aiohttp** (не long polling). Endpoint: `https://api.prideclub.fun/bot/webhook`.
- **Bot НЕ обращается к БД напрямую** — только через Backend API с `X-Service-Token` JWT.
- **Bot НЕ выставляет счета** (`bot.send_invoice` / `bot.create_invoice_link` в коде
  отсутствуют). На проде платежи = мок на фронте; backend и worker код для
  `process_payment` подготовлен, но контракт `internal_payments.py` имеет
  известный баг (`chat_id` vs `habit_id`). См. [09-prod-readiness.md](09-prod-readiness.md).
- Все "тяжёлые" операции делегируются через `POST /internal/*` на backend, который
  кладёт задачи в Celery. Бот НЕ пишет в Redis напрямую.

## 5. Worker — фоновые задачи (apps/worker)

```
worker/
├── worker/
│   ├── celery_app.py              # Celery 5.4, broker=redis://redis:6379/1, --pool=solo
│   ├── config.py                  # pydantic-settings
│   ├── logging_setup.py           # structlog JSON
│   │
│   ├── tasks/
│   │   ├── process_checkin.py           # ad-hoc: от backend через send_task("checkin", ...)
│   │   ├── process_penalty.py           # ad-hoc: от backend через send_task("penalty", ...)
│   │   ├── process_payment.py           # ad-hoc: подготовлен, контракт сломан, в MVP не вызывается
│   │   ├── close_catch_window.py        # cron: crontab(minute=5) каждый час
│   │   └── close_season.py              # cron: crontab(hour=5, minute=0)
│   │
│   │   # REMOVED Phase 8 (cleanup bonus mechanics, 2026-08-21):
│   │   # - apply_catch_bonus.py
│   │   # - expire_bonus_points.py
│   │   # - integrity_check_bonus_transactions.py
│   │
│   └── beat_schedule.py
│
├── db/
│   └── session.py                 # собственный async_session_factory, дублирует apps/backend/app/db/session.py
│
├── tests/                         # 34 тест (2 legacy fail в test_close_catch_window.py)
├── pyproject.toml
└── requirements.txt
```

**Ключевые решения:**
- **Celery 5.4** + Redis 7 (broker `redis://redis:6379/1`, result backend `:6379/2`).
- **`--pool=solo`** — один процесс, async внутри. Блокирует горизонтальное масштабирование;
  замена на `prefork` отложена до роста.
- **8 ad-hoc тасок + 4 cron-таски** (см. таблицу в [02-architecture.md](02-architecture.md) §2).
- **Идемпотентность** через уникальные индексы в БД: `checkins(membership_id, date)`,
  `penalties(membership_id, date, reason)`, `transactions.idempotency_key`.

## 6. Инфраструктура (infra/)

```
infra/
├── docker/
│   ├── frontend.Dockerfile   # multi-stage: node:20-alpine → nginx:1.27-alpine
│   ├── backend.Dockerfile    # python:3.12-slim
│   ├── bot.Dockerfile        # python:3.12-slim
│   └── worker.Dockerfile     # python:3.12-slim
├── nginx/                    # референсные конфиги (на проде nginx на хосте, не в контейнере)
│   ├── nginx.conf
│   ├── frontend.nginx.conf
│   ├── nginx.prideclub.conf
│   ├── nginx.prod.conf
│   └── prideclub.tls.conf
├── postgres/
│   └── postgresql-tuning.conf
├── backup/                   # backup_cron.sh готов, НЕ развёрнут (см. 07-security-and-ops.md §4)
│   ├── backup_cron.sh
│   ├── restore_test.sh
│   └── rotate_backups.py
├── scripts/
│   ├── setup_tls_prideclub.sh
│   └── ssh_to_vps.sh
├── deploy.sh                 # деплой: rsync + build + migrate + up -d + register webhook
├── setup_server.sh           # первоначальная подготовка Ubuntu 24.04
└── docker-compose.yml        # 7 сервисов: postgres, redis, backend, bot, worker, frontend, pgweb
```

### Сервисы docker-compose (7)

- `postgres` (`postgres:16-alpine`) — основная БД, volume `pgdata`
- `redis` (`redis:7-alpine`, AOF, 256MB cap) — кэш + Celery broker + result backend
- `backend` (`habit-club-backend`) — FastAPI 0.115 + uvicorn ×2, `/health` 200
- `bot` (`habit-club-bot`) — aiogram 3.30 + aiohttp webhook, `:8080`
- `worker` (`habit-club-worker`) — Celery 5.4 + `--pool=solo`, 8 tasks + 4 cron
- `frontend` (`habit-club-frontend`) — multi-stage build, nginx 1.27, отдаёт статику на 80
- `pgweb` (`sosedoff/pgweb`) — UI к БД на `db.prideclub.fun` (basic auth)

**Сеть:** одна bridge `habit-club_default` (172.18.0.0/16), DNS по именам сервисов.
**Volumes:** `pgdata`, `redisdata`, `club_uploads` (расшарен между backend и frontend).

Подробности по deploy-процедуре — в [10-deploy.md](10-deploy.md).

## 7. Порядок разработки MVP

1. **Инфраструктурный фундамент** (1–2 дня):
   - VPS в РФ, Docker, firewall, Let's Encrypt.
   - PostgreSQL + Redis в Docker.
   - Backup-скрипты, мониторинг.
   - CI (lint + test).

2. **Безопасность и аутентификация** (1–2 дня):
   - `validate_init_data`, `validate_service_token`, middleware.
   - `/health`, `/ready`.
   - Все миграции (`000_extensions.sql` и далее).

3. **Backend + Bot: чек-ины** (3–4 дня):
   - Обработка `/start`, вступление в клуб.
   - Приём кружков, антифрод-валидация медиа.
   - Worker `process_checkin` с уникальным индексом `(membership_id, date)`.

4. **Backend + Worker: штрафы** (2–3 дня):
   - `process_penalty` с `FOR UPDATE`, идемпотентностью, начислением в `prize_pool`.
   - `close_catch_window` с `INSERT ... ON CONFLICT DO NOTHING`.
   - `suspicious_pairs` автоматическое смягчение.

5. **Mini App: базовые экраны** (3–4 дня):
   - Маркетплейс привычек.
   - "Сегодня" с таймером и статусом.
   - "Участники" с кнопкой "Спалить".

6. *DONE/REMOVED Phase 8:* BonusService (бонусные поинты) и сезоны.
   - Виртуальные бонусы удалены в Phase 8 (2026-08-21).
   - `season_prize_rules`, `close_season` остаются как часть сезонной механики.

7. **Платежи** (2–3 дня):
   - Telegram Payments с `idempotency_key` на `telegram_payment_charge_id`.
   - Пополнение депозита, вывод.

8. **Лидерборд, баланс, профиль** (2–3 дня):
   - Топ-3, "Охотники", "Доска позора".
   - История операций.

9. **Тест-план из 14 сценариев** (параллельно + 1 день на нагрузочный).

10. **Полировка UI/UX** (по дизайн-концепции), haptic feedback, анимации.

11. **Наблюдаемость** (параллельно):
    - Продуктовые метрики (см. [02-architecture.md](02-architecture.md)).
    - Алерты в Telegram.
    - Sentry.
