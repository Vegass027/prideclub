# Habit Club — быстрый старт разработки

## Что это

Telegram Mini App для дисциплины по привычкам. Закрытые клубы (планка, ранний подъём,
чтение) с денежными штрафами за пропуски, социальным контролем и сезонными призами.

**Полная документация:** [docs/](docs/)

## Требования

- Python 3.11+
- Node.js 20+
- Docker + Docker Compose
- PostgreSQL 14+ (через Docker, локально не нужен)
- Telegram Bot Token (получить у [@BotFather](https://t.me/BotFather))
- Make

## Первый запуск (5 минут)

```bash
# 1. Клонировать
git clone <repo-url> habit-club && cd habit-club

# 2. Создать .env из шаблона
cp .env.example .env
# Заполнить BOT_TOKEN, SERVICE_SECRET (любая длинная случайная строка),
# POSTGRES_PASSWORD

# 3. Поднять всё окружение
make dev
# Это запустит postgres, redis, backend, bot, worker в Docker

# 4. Применить миграции (включая 000_extensions.sql)
make migrate

# 5. Проверить что работает
curl http://localhost:8000/health    # → {"status":"ok"}
curl http://localhost:8000/ready     # → {"status":"ready"}
```

Mini App (frontend) запускается отдельно:

```bash
cd apps/frontend
npm install
npm run dev
# Откроется на http://localhost:5173
```

## Структура репозитория

```
habit-club/
├── apps/
│   ├── frontend/         # Telegram Mini App (React + TS)
│   ├── backend/          # Backend API (FastAPI)
│   ├── bot/              # Bot Gateway (aiogram)
│   └── worker/           # Celery workers
├── packages/
│   └── shared/           # Общие типы
├── infra/                # docker-compose, nginx, backup
├── docs/                 # Полная документация
└── Makefile
```

Подробности в [docs/03-project-structure.md](docs/03-project-structure.md).

## Команды

### Разработка

```bash
make dev              # Поднять всё окружение
make down             # Остановить
make logs             # Смотреть логи всех сервисов
make logs-backend     # Только backend
make shell-backend    # Зайти в контейнер backend
make restart-backend  # Перезапустить только backend
```

### Тесты и линт

```bash
make test             # Прогнать pytest (unit + integration)
make lint             # ruff + mypy
make format           # black + ruff --fix
```

### Миграции и бэкапы

```bash
make migrate          # Применить миграции
make migrate-test     # Тест: upgrade → downgrade → upgrade
make migrate-new      # Создать новую миграцию (alembic revision)
make backup           # Создать бэкап вручную
make backup-test      # Тестовый restore из последнего бэкапа
```

### Деплой

```bash
make deploy           # Деплой на VPS (требует SSH_KEY и SERVER)
make logs-prod        # Логи с прода
make status           # Статус сервисов на проде
```

## Definition of Done для user story

Каждый PR/PR-merge считается завершённым, когда:

- [ ] Код покрыт unit-тестами (happy path + минимум 1 edge case)
- [ ] Миграции применяются и откатываются без ошибок (`make migrate-test`)
- [ ] Нет прямого использования `float`/`Decimal` для денежных полей — только `int`
- [ ] Middleware-проверки (initData / service-token) не обойдены в новых эндпоинтах
- [ ] PII не попадает в логи (проверка вручную по `git diff`)
- [ ] Все публичные методы логируют начало/конец с `duration_ms`
- [ ] Критические операции (штрафы, платежи, бонусы) пишут структурированные события
- [ ] Новые эндпоинты регистрируют счётчики запросов и ошибок в Prometheus
- [ ] Прошёл `make lint` и `make test` в CI

## Где искать информацию

| Вопрос | Документ |
|---|---|
| Что делаем и зачем | [docs/01-concept.md](docs/01-concept.md) |
| Как устроена система | [docs/02-architecture.md](docs/02-architecture.md) |
| Структура кода | [docs/03-project-structure.md](docs/03-project-structure.md) |
| Как писать код | [docs/04-code-standards.md](docs/04-code-standards.md) |
| Дизайн и экраны | [docs/05-ui-ux.md](docs/05-ui-ux.md) |
| Схема БД, миграции, антифрод | [docs/06-data-model.md](docs/06-data-model.md) |
| Безопасность, ФЗ-152, бэкапы, мониторинг | [docs/07-security-and-ops.md](docs/07-security-and-ops.md) |

## Что делать в первую очередь

1. **Прочитать** [docs/01-concept.md](docs/01-concept.md) и
   [docs/02-architecture.md](docs/02-architecture.md).
2. **Поднять dev-окружение** по инструкции выше.
3. **Применить миграции** из [docs/06-data-model.md](docs/06-data-model.md).
4. **Следовать порядку разработки** из
   [docs/03-project-structure.md §7](docs/03-project-structure.md#7-порядок-разработки-mvp).

## Переменные окружения (.env)

```bash
# Telegram
BOT_TOKEN=                 # от @BotFather
WEBHOOK_SECRET=            # любой случайный токен для верификации webhook

# Backend
SERVICE_SECRET=            # общий секрет для service-token (HS256)

# Database
POSTGRES_DB=habits
POSTGRES_USER=habits
POSTGRES_PASSWORD=         # случайный сложный пароль
DATABASE_URL=postgresql+asyncpg://habits:${POSTGRES_PASSWORD}@postgres:5432/habits

# Redis
REDIS_URL=redis://redis:6379/0

# Backups (Selectel S3)
S3_ENDPOINT_URL=https://s3.ru-1.storage.selcloud.ru
S3_BACKUP_BUCKET=habits-backups
S3_ACCESS_KEY=
S3_SECRET_KEY=
BACKUP_PUBLIC_KEY=         # age public key для шифрования
```

## Тест-план (обязательные сценарии)

Перед каждым релизом прогонять 14 сценариев из
[docs/06-data-model.md §4](docs/06-data-model.md#4-антифрод) и
[docs/06-data-model.md §5](docs/06-data-model.md#5-race-conditions-и-идемпотентность).
Тесты должны быть зелёными в CI.

## Когда застрял

1. Поискать в `docs/` (полнотекстовый поиск по содержимому).
2. Проверить логи: `make logs` или `make logs-backend`.
3. Проверить `/ready` — может быть проблема с БД/Redis.
4. Посмотреть архив [docs/archive/](docs/archive/) — там итерационные документы
   ревью с обоснованиями решений.

## Лицензия

Proprietary (закрытый проект).
