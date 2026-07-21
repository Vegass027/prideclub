# Habit Club — Статус бэкенда и план до прода

> Дата среза: 2026-07-21
> Версия: a08f08a (main)
> Сервер: Contabo Cloud VPS 4 (4 vCPU / 8 GB / 100 GB SSD), `169.58.52.78`
> Документ описывает только **бэкенд** (FastAPI + worker + bot). Фронт — отдельная тема.

---

## 1. Текущая стадия: **~85% готовности к проду**

### 1.1 Что полностью работает (есть на проде, проверено E2E)

| # | Подсистема | Статус | Где проверено |
|---|-----------|--------|---------------|
| 1 | **Аутентификация** initData + JWT для /internal | ✅ Работает | E2E на сервере, 54 backend теста |
| 2 | **Чек-ины** через Celery worker | ✅ Работает | `worker_checkin_ok` в логах, идемпотентность по `(membership_id, date)` |
| 3 | **Кэтчер-механика** через worker | ✅ Работает | `process_penalty` через `/internal/penalties/catch` → penalty + transaction в БД |
| 4 | **Telegram Payments webhook** | ✅ Работает | `process_payment` идемпотентно через `charge_id` |
| 5 | **Депозит + штрафы** | ✅ Работает | FK-фикс (penalty → transaction в одной транзакции) |
| 6 | **Bonus-система** (catch bonus, expire) | ✅ Работает | `apply_catch_bonus`, `expire_bonus_points` |
| 7 | **Celery Beat** (close_catch_window каждый час в :05) | ✅ Работает | `crontab(minute=5)` в `celery_app.py` |
| 8 | **Sentry + Prometheus** | ✅ Инициализируются (no-op без DSN) | `/metrics` endpoint отдаёт метрики |
| 9 | **PostgreSQL** (6 миграций, расширения) | ✅ Работает | `000_extensions` → `006_suspicious_pairs_index` |
| 10 | **Redis** (catch rate-limit Lua, today cache) | ✅ Работает | `catch_rate_limiter.py`, `today_cache.py` |
| 11 | **Antifraud** (suspicious_pairs, proof validation) | ✅ Работает | `suspicious_pairs_service.py` |
| 12 | **Season prize distribution** | ✅ Работает | `close_season` через worker |

### 1.2 Тесты

| Пакет | Локально | На сервере |
|-------|----------|------------|
| `apps/backend/tests` | **55 passed** | **54 passed, 1 skipped** |
| `apps/worker/tests` | **32 passed** | **32 passed** |
| **Итого** | **87 passed** | **86 passed, 1 skipped** |

E2E через Celery подтверждён: чек-ин → `worker.tasks.process_checkin.run` → DB. В ответе `duplicate: True` при повторе.

### 1.3 Известные проблемы, которые НЕ блокируют прод

| # | Что | Влияние |
|---|-----|---------|
| 1 | `pytest-asyncio` deprecation warning про `asyncio_default_fixture_loop_scope` | warning в CI, не ошибка |
| 2 | `lupa==2.2.4` не существует — пин на `2.2` (фикс уже в репо) | уже исправлено |
| 3 | `asyncio.run()` из running event loop — старые тесты вызывают `run()` Celery-обёртку; переписаны на `_process()` | уже исправлено |
| 4 | `gen_random_uuid()` в SQLite — решено через `before_cursor_execute` event listener | уже исправлено |
| 5 | `DomainError.__init__` не принимал `code=` — добавлено | уже исправлено |

---

## 2. Что осталось сделать для **100% готовности к проду**

### 2.1 🔴 Критический блокер (без этого **нельзя запускать для пользователей**)

#### Шаг 1. Sentry DSN в проде
- **Что**: Получить DSN в Sentry.io, положить в `.env` на сервере
- **Файл**: `apps/backend/app/core/observability.py` уже вызывает `sentry_sdk.init(dsn=...)` — нужно только задать переменную
- **Проверка**: дождаться первого исключения в проде, убедиться что появилось в Sentry
- **Время**: 15 минут
- **Ответственный**: Дмитрий (получает DSN), я (проверяю интеграцию)

#### Шаг 2. CI в GitHub Actions — реально запустить
- **Что**: workflow `backend-ci.yml` уже написан, но не проверен что он реально зелёный на GitHub
- **Файл**: `.github/workflows/backend-ci.yml`
- **Что должно быть**: ruff + mypy + pytest на PR + worker job
- **Проверка**: посмотреть последний прогон в GitHub Actions UI
- **Время**: 30 минут (включая фиксы если что-то упало только в CI)
- **Ответственный**: я

#### Шаг 3. Домен + HTTPS для Mini App
- **Что**: Mini App открывается по HTTPS-домену. Бот уже задеплоен (нужно проверить), но домен для веба не настроен
- **Файл**: `infra/nginx/` или настройка в `docker-compose.yml` (нет сейчас)
- **Что должно быть**: домен → Cloudflare → Contabo IP, валидный TLS-сертификат (Let's Encrypt)
- **Проверка**: открыть `https://app.example.com` — Mini App загружается
- **Время**: 1-2 часа (включая настройку DNS + certbot)
- **Ответственный**: Дмитрий (домен + DNS), я (nginx + certbot)

#### Шаг 4. Telegram Bot webhook на проде
- **Что**: Бот должен работать через webhook (по AGENTS.md), сейчас неизвестно в каком он состоянии
- **Файл**: `apps/bot/bot/main.py` — нужно проверить что бот реально стартует и webhook регистрируется
- **Проверка**: отправить `/start` боту → он отвечает; посмотреть логи `habit-bot` контейнера
- **Время**: 30 минут
- **Ответственный**: я

### 2.2 🟡 Важный блокер (можно запустить для soft-launch / тест-группы)

#### Шаг 5. Backup PostgreSQL по расписанию
- **Что**: `pg_dump` каждый день, retention 30 дней, off-site (Selectel S3 или B2)
- **Файл**: ещё нет — нужно создать `infra/backup/backup.sh` + cronjob
- **Что должно быть**: ежедневный `pg_dump | gzip | upload to s3` + проверка что restore работает
- **Проверка**: упасть на тестовом сервере, восстановиться из backup, убедиться что данные на месте
- **Время**: 2 часа
- **Ответственный**: я

#### Шаг 6. Мониторинг (алерты)
- **Что**: Prometheus собирает метрики (`/metrics`), но **алертов в Alertmanager / Telegram нет**
- **Файл**: ещё нет — нужно создать `infra/prometheus/alerts.yml` + bot для отправки в TG
- **Минимальные алерты**:
  - Backend down > 1 минуты
  - Worker queue > 100 задач
  - PostgreSQL connections > 80% от лимита
  - Redis down
  - 5xx error rate > 1% за 5 минут
- **Время**: 2 часа
- **Ответственный**: я

#### Шаг 7. Rate-limit для публичных endpoints
- **Что**: catch rate-limit есть (Redis Lua), но **общий HTTP rate-limit для `/api/v1/*` не настроен**
- **Файл**: `apps/backend/app/core/middleware.py` — добавить Redis-based rate-limit (например, 60 req/min на user_id)
- **Что должно быть**: 429 Too Many Requests при превышении
- **Время**: 1 час
- **Ответственный**: я

#### Шаг 8. Логирование в JSON
- **Что**: текущие логи — текст. В проде лучше structured JSON для парсинга
- **Файл**: `apps/backend/app/core/logging.py` — добавить JSON formatter
- **Время**: 1 час
- **Ответственный**: я

### 2.3 🟢 Не блокирует, но нужно до широкого запуска

#### Шаг 9. ФЗ-152 compliance — финальная проверка
- **Что**: согласие пользователя, право на удаление, хранение в РФ
- **Файл**: `apps/backend/app/api/v1/users.py` — endpoint `DELETE /users/me` для GDPR
- **Проверка**:
  - Кнопка "Удалить аккаунт" в Mini App вызывает этот endpoint
  - Все PII (first_name, username, photo_url) удаляются, остаётся `user_id` для foreign keys
  - БД расположена в Selectel (РФ) — сейчас на Contabo (Германия)
- **Время**: 4 часа (включая перенос БД в Selectel)
- **Ответственный**: Дмитрий (решает про Selectel), я (код)

#### Шаг 10. Перенос PostgreSQL в Selectel (РФ)
- **Что**: AGENTS.md требует хранение ПДн в РФ. Contabo — Германия, формально нарушение
- **Что нужно**: managed PostgreSQL в Selectel, миграция данных, переключение `DATABASE_URL`
- **Время**: 1 день (включая провайдер-сайн-ап)
- **Ответственный**: Дмитрий (аккаунт Selectel), я (миграция + проверка)

#### Шаг 11. Load testing
- **Что**: не знаем как бэкенд держит 1000 одновременных пользователей
- **Инструмент**: `locust` или `k6`
- **Сценарий**: симулировать 1000 пользователей которые делают чек-ин одновременно
- **Проверка**: p99 latency < 500ms, нет 5xx
- **Время**: 3 часа
- **Ответственный**: я

#### Шаг 12. Документация API
- **Что**: FastAPI генерит Swagger UI на `/docs` — но нужно проверить что он актуален
- **Файл**: `/docs`, `/redoc` endpoint'ы уже есть
- **Что нужно**: каждый endpoint имеет description + примеры, нет 500 ошибок в Swagger
- **Время**: 2 часа
- **Ответственный**: я

### 2.4 ⚪ Пост-launch (можно отложить)

| Шаг | Что | Время |
|-----|-----|-------|
| 13 | A/B-тесты | 1 неделя |
| 14 | Сезонная логика — приз-фонды автоматически | 1 день |
| 15 | Admin-панель (web UI) для операторов | 1 неделя |
| 16 | Перевод бэкенда на gRPC для внутренних вызовов | 2 недели |
| 17 | Шардирование PostgreSQL по habit_id | 2 недели |

---

## 3. Технический долг (не блокер)

| Что | Файл | Что сделать |
|-----|------|------------|
| `on_event` deprecation в FastAPI | `apps/backend/app/main.py` | Перевести на `lifespan` context manager |
| `apply_catch_bonus.run()` без параметров | `apps/worker/worker/tasks/apply_catch_bonus.py` | Добавить DI через session_factory (как в process_penalty) |
| `_remap_postgres_types_for_sqlite` мутирует типы колонок глобально | `apps/worker/tests/conftest.py` | Использовать `event.listens_for` + копирование таблиц вместо мутации моделей |
| `redis_port=None` в worker — rate-limit выключен | `apps/worker/worker/tasks/process_penalty.py` | Сделать rate-limit обязательным, fallback — fail-closed |

---

## 4. Деплой-чеклист (перед открытием для пользователей)

```
□ Sentry DSN настроен и работает
□ TLS-сертификат валиден (https://app.example.com)
□ DNS указывает на Contabo IP
□ Telegram bot webhook зарегистрирован
□ BACKUP_DAILY=true + cron установлен
□ Тестовый restore из backup прошёл успешно
□ Alertmanager → Telegram alerts работают (smoke-test)
□ Rate-limit на /api/v1/* активен
□ Логи в JSON формате
□ Документация API актуальна (/docs)
□ Нагрузочный тест пройден (1000 RPS, p99 < 500ms)
□ Политика конфиденциальности + удаление аккаунта работают
□ Все env-переменные в .env (chmod 600), не в репо
□ .env НЕ закоммичен (проверить git log)
□ SSH root доступ закрыт (только ключ или ограниченный пользователь)
□ fail2ban установлен на сервере
□ ufw только 22, 80, 443
□ PostgreSQL НЕ слушает 0.0.0.0 (только unix socket / localhost)
```

---

## 5. Сводный план по времени

| Фаза | Задачи | Время |
|------|--------|-------|
| **Критический блокер** (Шаги 1-4) | Sentry + CI + домен + bot webhook | 1 рабочий день |
| **Soft-launch готовность** (Шаги 5-8) | Backups + мониторинг + rate-limit + JSON логи | 1 рабочий день |
| **Широкий запуск** (Шаги 9-12) | ФЗ-152 + Selectel + load-test + docs | 1 неделя |
| **Пост-launch** | Тех-долг + новые фичи | по необходимости |

**Итого до широкого запуска для пользователей**: ~1.5 недели при условии что Дмитрий решает вопросы с Sentry/доменом/Selectel параллельно.

---

## 6. Контакты и ownership

| Зона | Ответственный |
|------|--------------|
| Код (Python, SQL, инфра) | AI-ассистент (я) |
| Инфраструктура (домен, DNS, Selectel, Sentry) | Дмитрий |
| Продуктовые решения (приоритеты, фичи) | Дмитрий |
| Юридическое (ФЗ-152, политика конфиденциальности) | Дмитрий |

---

## 7. Что НЕ нужно делать прямо сейчас

- ❌ Не начинать новые фичи (новые типы привычек, marketplace) — фокус на стабилизации
- ❌ Не оптимизировать производительность до load-test
- ❌ Не рефакторить рабочий код без причины (тех-долг задокументирован, не горит)
- ❌ Не менять архитектуру (слои api/services/repositories выдержали проверку)

---

**Ключевая мысль**: бэкенд **технически работает на проде** (E2E через Celery подтверждён, тесты зелёные). Но до того, как открыть для пользователей, **нужно закрыть критический блокер** (Sentry, домен, бот, CI) — это 1 день работы. После этого можно запускать soft-launch для тест-группы.
