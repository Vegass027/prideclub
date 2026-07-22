# AGENTS.md — инструкции для AI-агентов и ассистентов

## О проекте

Habit Club — Telegram Mini App для дисциплины по привычкам с денежными штрафами.

## Структура документации

Читать последовательно перед любыми изменениями:

1. `docs/01-concept.md` — что делаем и зачем (продуктовая логика)
2. `docs/02-architecture.md` — общая архитектура (стек, потоки, масштабирование)
3. `docs/03-project-structure.md` — структура репозитория
4. `docs/04-code-standards.md` — паттерны кода (layered architecture, DI, исключения)
5. `docs/05-ui-ux.md` — дизайн-концепция
6. `docs/06-data-model.md` — схема БД, антифрод, идемпотентность
7. `docs/07-security-and-ops.md` — аутентификация, ФЗ-152, бэкапы, мониторинг
8. `docs/08-readme.md` — стартовая инструкция для разработчика

## Ключевые правила для агентов

### Стек
- **Backend:** Python 3.12, FastAPI 0.115, SQLAlchemy 2.0 async, asyncpg 0.30, Alembic 1.14, Pydantic 2.10, structlog 24
- **Bot:** aiogram 3.30, aiohttp 3.13 (webhook, не long polling), PyJWT 2.10
- **Worker:** Celery 5.4 + Redis (broker `redis://redis:6379/1`), `--pool=solo`, structlog 24
- **Frontend:** React 18 + TypeScript, Vite 6, TailwindCSS 3, React Query 5, Zustand 5, React Router 6, `@telegram-apps/sdk` 3.3
- **БД:** PostgreSQL 16, Redis 7
- **Nginx (reverse proxy):** на хосте (Ubuntu 24.04), не в контейнере; плюс nginx 1.27-alpine внутри `habit-club-frontend` (multi-stage build)

### Архитектурные принципы
1. **Layered Architecture:** api → services → repositories → models. Не перепрыгивать слои.
2. **DI через конструктор** — все зависимости сервиса через `__init__`, никакого глобального состояния.
3. **Одна транзакция = один handler.** Сервисы НЕ вызывают `session.commit()`.
4. **Async I/O везде.** Никаких `time.sleep`, `requests`, sync file I/O.
5. **Доменные исключения** с глобальным обработчиком, не try/except в роутах.
6. **Константы и enum'ы** в `core/constants.py`, не магические числа.
7. **Frontend не вызывает fetch/axios напрямую** — только через хуки над `shared/api`.
8. **Все суммы — `int` (копейки).** Никогда `float`/`Decimal` для денег.
9. **PII не логируется.** Только `user_id` (числовой).

### Безопасность
- `user_id` берётся ТОЛЬКО из `request.state.telegram_user` (после валидации initData).
- Никогда не принимать `user_id` параметром запроса.
- Для bot/worker используется JWT service-token с `aud`/`iss`/`exp`.
- Секреты в `.env` с `chmod 600`, не в репозитории. SSH-пароль — в
  `~/.config/kilo/privichki-bootstrap.md` (вне репо), ни в каких коммитах.
- Сервер на 2026-07-22 — Contabo (Германия), не РФ. Миграция на Selectel / Yandex
  Cloud — план; см. [07-security-and-ops.md](docs/07-security-and-ops.md) §1 и
  [09-prod-readiness.md](docs/09-prod-readiness.md) §1.

### Антифрод (обязательно)
- Один чек-ин в сутки (уникальный индекс).
- Валидация медиа (тип, длительность, не forwarded).
- `FOR UPDATE` при списании депозита.
- Идемпотентность через `idempotency_key` для штрафов и платежей.
- `suspicious_pairs` для защиты от сговора.

### Definition of Done
Перед merge любого изменения проверить:
- [ ] Unit-тесты (happy + edge case)
- [ ] `make migrate-test` проходит
- [ ] Нет `float`/`Decimal` для денег
- [ ] Middleware не обойден
- [ ] PII не в логах
- [ ] Логи + метрики на критических операциях
- [ ] `make lint` + `make test` в CI

## Чего НЕ делать

- ❌ Не переписывать документацию без явной просьбы — она консолидирована.
- ❌ Не добавлять бизнес-логику в роуты/хендлеры.
- ❌ Не использовать `any` в TypeScript (только с обоснованием).
- ❌ Не хардкодить строки-статусы — только через enum'ы.
- ❌ Не создавать новые таблицы без миграции Alembic.
- ❌ Не логировать `telegram_user.first_name` / `username`.
- ❌ Не менять правила ФЗ-152 (хранение в РФ, согласие, право на удаление).
- ❌ Не добавлять синхронный I/O в async-код.

## Работа с архивом

`docs/archive/` содержит 6 итераций ревью (`01_initial_review.md` ... `06_dorabotki_v6.md`).
Это **история решений** — почему что-то сделано именно так. Если меняешь решение,
сверься с архивом: возможно, был отклонён какой-то вариант с обоснованием.
