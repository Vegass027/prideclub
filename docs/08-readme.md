# 08 — README и старт разработки

Краткий входной документ для разработчика. Полная версия — в [`/README.md`](../README.md).

---

## Что это

Telegram Mini App для дисциплины по привычкам. Закрытые клубы (планка, ранний подъём,
чтение) с денежными штрафами за пропуски, социальным контролем и сезонными призами.

## Требования

- Python 3.12+ (Dockerfile пинёт `python:3.12-slim`)
- Node.js 20+
- Docker + Docker Compose
- PostgreSQL 16+ (через Docker)
- Make

## VPS для продакшена

Минимальная конфигурация, прошедшая нагрузочный smoke-test:

| Параметр | Значение |
|---|---|
| OS | **Ubuntu 24.04 LTS** (Noble), x86_64 |
| CPU | 2 ядра, 3.0–3.3 GHz |
| RAM | 4 ГБ DDR4/NVMe-friendly |
| Диск | 40 ГБ NVMe SSD |
| Сеть | 1 Гбит/с (RU-регион, Selectel) |
| Swap | 1 ГБ на NVMe (для пиков) |

Бюджет RAM (ЦУП на пике ~ 2.4 ГБ из 4 ГБ → 1.6 ГБ запаса):
- Postgres 16 (tuned):  ~550 МБ peak
- Redis 7 (256 МБ cap): ~180 МБ peak
- Backend (uvicorn × 2): ~700 МБ peak
- Worker (Celery × 2):   ~500 МБ peak
- Bot (aiogram):         ~220 МБ peak
- Frontend (nginx SPA):  ~60 МБ  peak
- ОС + nginx + logs:     ~180 МБ peak

### Подготовка сервера (Ubuntu 24.04, root/sudo)

```bash
# Один раз после получения сервера:
scp infra/setup_server.sh root@host:/root/
ssh root@host "bash /root/setup_server.sh"
# Ставит: Docker CE 27+, swap 1GB, ufw, fail2ban, chrony, sysctl-тюнинг, log rotation
```

### Деплой

```bash
# С локальной машины:
cp .env.example .env
# Заполнить BOT_TOKEN, SERVICE_SECRET, POSTGRES_PASSWORD, S3_* ключи
sed -i '' "s/POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i '' "s/SERVICE_SECRET=.*/SERVICE_SECRET=$(openssl rand -hex 32)/" .env

SSH_KEY=~/.ssh/id_ed25519 SERVER=ubuntu@<host> make deploy
# или:
./infra/deploy.sh ubuntu@<host> ~/.ssh/id_ed25519
```

`deploy.sh` синхронизирует код через rsync, собирает образы, прогоняет миграции, поднимает стек, проверяет `/health`, регистрирует Telegram webhook.

## Первый запуск (5 минут)

```bash
git clone <repo-url> habit-club && cd habit-club
cp .env.example .env
# Заполнить BOT_TOKEN, SERVICE_SECRET (случайная строка), POSTGRES_PASSWORD

make dev           # postgres, redis, backend, bot, worker
make migrate       # применить все миграции (включая 000_extensions.sql)

curl http://localhost:8000/health    # → {"status":"ok"}
curl http://localhost:8000/ready     # → {"status":"ready"}
```

Frontend отдельно:

```bash
cd apps/frontend && npm install && npm run dev
# http://localhost:5173
```

## Структура репозитория

```
habit-club/
├── apps/
│   ├── frontend/         # Telegram Mini App (React + TS)
│   ├── backend/          # Backend API (FastAPI)
│   ├── bot/              # Bot Gateway (aiogram)
│   └── worker/           # Celery workers
├── packages/shared/      # Общие типы
├── infra/                # docker-compose, nginx, backup
├── docs/                 # Эта документация
└── Makefile
```

Детали — в [03-project-structure.md](03-project-structure.md).

## Команды

### Разработка

```bash
make dev              # Поднять всё окружение
make down             # Остановить
make logs             # Логи всех сервисов
make logs-backend     # Только backend
make shell-backend    # Зайти в контейнер backend
make restart-backend  # Перезапустить backend
```

### Тесты и линт

```bash
make test             # pytest (unit + integration)
make lint             # ruff + mypy
make format           # black + ruff --fix
```

### Миграции и бэкапы

```bash
make migrate          # Применить миграции
make migrate-test     # Тест: upgrade → downgrade → upgrade
make migrate-new      # Создать миграцию (make migrate-new m="add field")
make backup           # Бэкап вручную
make backup-test      # Тестовый restore
```

## Definition of Done для user story

Каждый PR/PR-merge считается завершённым, когда:

- [ ] Код покрыт unit-тестами (happy + минимум 1 edge case)
- [ ] Миграции применяются и откатываются без ошибок (`make migrate-test`)
- [ ] Нет `float`/`Decimal` для денежных полей — только `int`
- [ ] Middleware (initData / service-token) не обойден в новых эндпоинтах
- [ ] PII не попадает в логи (проверка вручную по `git diff`)
- [ ] Все публичные методы логируют начало/конец с `duration_ms`
- [ ] Критические операции (штрафы, платежи, бонусы) пишут структурированные события
- [ ] Новые эндпоинты регистрируют счётчики запросов/ошибок в Prometheus
- [ ] Прошёл `make lint` и `make test` в CI

## Где искать информацию

| Вопрос | Документ |
|---|---|
| Что делаем и зачем | [01-concept.md](01-concept.md) |
| Как устроена система | [02-architecture.md](02-architecture.md) |
| Структура кода | [03-project-structure.md](03-project-structure.md) |
| Как писать код | [04-code-standards.md](04-code-standards.md) |
| Дизайн и экраны | [05-ui-ux.md](05-ui-ux.md) |
| Схема БД, миграции, антифрод | [06-data-model.md](06-data-model.md) |
| Безопасность, ФЗ-152, бэкапы, мониторинг | [07-security-and-ops.md](07-security-and-ops.md) |

## Когда застрял

1. Поискать в `docs/` (полнотекстовый поиск).
2. `make logs` или `make logs-backend` — посмотреть логи.
3. Проверить `/ready` — может быть проблема с БД/Redis.
4. [docs/archive/](archive/) — история решений, иногда объясняет почему сделано именно так.
