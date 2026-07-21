# 03 — Структура проекта

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
│   ├── pages/
│   │   ├── Onboarding/
│   │   ├── Marketplace/
│   │   ├── Today/
│   │   ├── Members/
│   │   ├── Leaderboard/
│   │   ├── Balance/
│   │   └── Profile/
│   │
│   ├── widgets/          # Составные блоки (HabitCard, MemberRow, DepositWidget)
│   │
│   ├── shared/
│   │   ├── ui/           # Атомарные (Button, Badge, Timer, Modal, Toast, Skeleton)
│   │   ├── api/          # axios-клиент + react-query hooks
│   │   ├── telegram/     # Обёртка tma.js (initData, mainButton, haptics)
│   │   ├── theme/        # Цветовые токены, синхронизация с темой Telegram
│   │   ├── hooks/        # Общие хуки (useUser, useHabits, useCheckin)
│   │   └── types/        # TS-типы (синхронизированы с backend)
│   │
│   ├── assets/           # Иконки, изображения, Lottie
│   └── main.tsx
│
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.js
```

**Ключевые решения:**
- **Vite** — быстрый dev-сервер, оптимален для Mini Apps.
- **React Query** — кэширование, повторные запросы, синхронизация без перерисовок.
- **Zustand** — лёгкий глобальный стейт (текущий пользователь, выбранные привычки).
- **`shared/telegram/`** — изоляция вызовов Telegram SDK от остального кода.

## 3. Backend API (apps/backend)

```
backend/
├── app/
│   ├── main.py                    # Точка входа FastAPI
│   ├── core/
│   │   ├── config.py              # Pydantic Settings (переменные окружения)
│   │   ├── security.py            # validate_init_data, validate_service_token, JWT
│   │   ├── logging.py             # Структурированное логирование
│   │   ├── exceptions.py          # Доменные исключения + глобальный обработчик
│   │   └── constants.py           # Enums, конфиги (PenaltyConfig, MembershipStatus)
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── users.py
│   │       ├── habits.py
│   │       ├── memberships.py
│   │       ├── checkins.py
│   │       ├── penalties.py
│   │       ├── leaderboard.py
│   │       ├── payments.py
│   │       └── health.py          # /health, /ready
│   │
│   ├── models/                    # SQLAlchemy модели
│   ├── schemas/                   # Pydantic-схемы запросов/ответов
│   ├── services/                  # Бизнес-логика
│   │   ├── checkin_service.py
│   │   ├── penalty_service.py
│   │   ├── deposit_service.py
│   │   ├── bonus_service.py
│   │   ├── season_service.py
│   │   └── leaderboard_service.py
│   │
│   ├── repositories/              # Слой доступа к данным
│   ├── db/
│   │   ├── session.py             # Подключение к PostgreSQL (async)
│   │   └── redis.py               # Подключение к Redis
│   │
│   └── tasks/                     # Сериализация задач для Celery
│
├── alembic/
│   ├── versions/
│   └── env.py
├── tests/
├── requirements.txt
├── alembic.ini
└── pyproject.toml
```

**Ключевые решения:**
- **Слоистая архитектура:** `api → services → repositories → models`. Каждый слой знает
  только о слое ниже.
- **Async SQLAlchemy** для неблокирующей работы с БД.
- **Alembic** для версионирования схемы.
- **Pydantic** как единый источник правды для валидации.

## 4. Bot Gateway (apps/bot)

```
bot/
├── bot/
│   ├── main.py                    # Запуск aiogram-диспетчера
│   ├── handlers/
│   │   ├── start.py               # /start — приветствие, открытие Mini App
│   │   ├── checkin.py             # Обработка video_note/photo в чатах клубов
│   │   ├── payments.py            # Успешные платежи Telegram Payments
│   │   └── admin.py               # Команды администратора
│   │
│   ├── middlewares/
│   │   ├── membership_check.py
│   │   └── rate_limit.py
│   │
│   ├── services/
│   │   └── api_client.py          # Клиент Backend API (с service_token)
│   │
│   └── config.py
│
├── webhook_server.py
├── requirements.txt
└── Dockerfile
```

**Ключевые решения:**
- **Webhook-режим** (не long polling).
- **Bot Gateway не обращается к БД напрямую** — только через Backend API с `service_token`.
- Все "тяжёлые" операции делегируются в очередь, не обрабатываются синхронно.

## 5. Worker — фоновые задачи (apps/worker)

```
worker/
├── worker/
│   ├── celery_app.py              # Конфигурация Celery
│   ├── tasks/
│   │   ├── process_checkin.py
│   │   ├── close_catch_window.py  # Cron: фиксация нарушений без улова
│   │   ├── process_penalty.py     # Обработка штрафа
│   │   ├── recalculate_leaderboard.py
│   │   ├── close_season.py        # Cron: распределение призов
│   │   ├── expire_stale_bonus_points.py
│   │   └── send_notifications.py
│   │
│   └── beat_schedule.py
│
├── requirements.txt
└── Dockerfile
```

## 6. Инфраструктура (infra/)

```
infra/
├── docker/
│   ├── frontend.Dockerfile
│   ├── backend.Dockerfile
│   ├── bot.Dockerfile
│   └── worker.Dockerfile
├── nginx/
│   └── nginx.conf
├── backup/
│   ├── backup_cron.sh
│   ├── rotate_backups.py
│   └── restore_test.sh
└── docker-compose.yml
```

### Сервисы docker-compose

- `postgres` — основная БД
- `redis` — кэш и брокер очередей
- `backend` — FastAPI приложение
- `bot` — aiogram Bot Gateway
- `worker` — Celery worker + Beat
- `nginx` — reverse proxy + HTTPS (в проде)
- `frontend` — dev-сервер Vite (в проде собирается в статику)

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

6. **BonusService и сезоны** (2 дня):
   - Бонусные поинты за уловы, продление подписки.
   - `season_prize_rules`, `close_season` с автоматическим распределением.

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
