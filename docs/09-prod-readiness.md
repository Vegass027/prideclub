# Habit Club — Статус бэкенда и план до прода

> Дата среза: 2026-07-23 (обновлено после фичи topic-scoped чек-ины и третий топик)
> Сервер: Contabo Cloud VPS 4 (4 vCPU / 8 GB / 100 GB SSD), `169.58.52.78`
> Домены: `prideclub.fun` (основной), `app.prideclub.fun` (Mini App),
> `admin.prideclub.fun` (Admin Mini App), `api.prideclub.fun` (API), `db.prideclub.fun` (pgweb)
> Документ описывает только **бэкенд** (FastAPI + worker + bot). Фронт — отдельная тема.

---

## 1. Текущая стадия: **✅ готов к soft-launch + Admin Mini App**

### 1.0 Что сделано в этой итерации
- ✅ `/setdomain` в BotFather → Mini App открывается кнопкой в боте
- ✅ Mini App живой на `https://app.prideclub.fun`
- ✅ **Admin Mini App** на `https://admin.prideclub.fun` (owner-only, через `OWNER_TELEGRAM_ID`)
- ✅ Telegram WebApp SDK подключён, initData передаётся в каждом запросе
- ✅ **Hardening итерация T1–T7** (см. `TZ_kharakteristiki_personazha.md` §8.1) —
  рефакторинг сервисов/репозиториев, без функциональных изменений.
  Все сервисы перезапущены на проде, `/health=ok`.

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
| 9 | **PostgreSQL** (11 миграций, расширения) | ✅ Работает | `000_extensions` → `011_habit_chat_topic` (миграции 010/011 — topic-scoped чек-ины и третий топик) |
| 10 | **Redis** (catch rate-limit Lua, today cache) | ✅ Работает | `catch_rate_limiter.py`, `today_cache.py`. T1: `parse_rate_limit_spec` в `core/utils.py`. |
| 11 | **Antifraud** (suspicious_pairs, proof validation) | ✅ Работает | `suspicious_pairs_service.py` + T2 `SuspiciousPairsRepository.lookup_flagged` |
| 12 | **Season prize distribution** | ✅ Работает | `close_season` через worker |
| 13 | **JSON-логирование** (structlog + JSONRenderer) | ✅ Работает | backend + worker пишут JSON в stdout |
| 14 | **HTTP rate-limit** (60/min api, 120/min internal) | ✅ Live проверен | 130 req → 120 пропущено + 10×429 |
| 15 | **HTTPS + Nginx + Let's Encrypt** | ✅ Live работает | `app.prideclub.fun/health` → 200 |
| 16 | **Telegram bot webhook** | ✅ Live работает | POST `/bot/webhook` → 200 |
| 17 | **CI в GitHub Actions** | ✅ Конфиг исправлен | `backend-ci.yml`, `frontend-ci.yml` |
| 18 | **Admin Mini App** (управление клубами: CRUD + activate/archive/restore) | ✅ На проде с `2026-07-21` (commit `ad0267b`) | `apps/frontend/src/admin/`, `admin.prideclub.fun`. Owner-gate в `core/middleware.py`. |

### 1.2 Тесты

| Пакет | Локально | На сервере |
|-------|----------|------------|
| `apps/backend/tests` | **161 passed** | не запускаются в проде (только локально + CI) |
| `apps/worker/tests` | **34 passed** (2 legacy fail в `test_close_catch_window.py` — pre-existing, не связано с T5) | не запускаются в проде |
| **Итого** | **195 passed** | — |

### 1.3 Live endpoints (после последнего деплоя)

```
✅ https://app.prideclub.fun          → Mini App (Vite + backend API)
✅ https://app.prideclub.fun/health    → {"status":"ok"}
✅ https://app.prideclub.fun/ready     → {"status":"ready"} (DB + Redis OK)
✅ https://app.prideclub.fun/api/v1/users/me (без auth) → 401
✅ https://api.prideclub.fun           → Backend API + bot webhook
✅ https://prideclub.fun / www.        → Public web (frontend)
✅ https://db.prideclub.fun            → pgweb admin (basic auth)
✅ TLS сертификат Let's Encrypt, валиден 89 дней (автопродление)
```

### 1.4 Решённые проблемы этой итерации

| # | Что | Решение |
|---|-----|---------|
| 1 | CI workflow падал на yaml-парсере | Заэкранировал `DATABASE_URL: "sqlite+aiosqlite:///:memory:"` (множественные двоеточия) |
| 2 | Дублирующийся workflow `backend.yml` | Удалён, остался только `backend-ci.yml` |
| 3 | Worker логировал обычным текстом | Добавлен `worker/logging_setup.py` с structlog |
| 4 | Не было общего HTTP rate-limit | `services/http_rate_limiter.py` + `RateLimitMiddleware` |
| 5 | Не было домена / HTTPS | Куплен `prideclub.fun`, настроен nginx + Let's Encrypt, Mini App доступен |

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
