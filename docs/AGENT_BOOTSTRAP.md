# AGENT_BOOTSTRAP — точка входа для нового AI-агента

> Этот документ — **главная точка входа**. Прочти его первым делом при старте
> нового чата. Он говорит, **что прочитать дальше**, **как подключаться к серверу**,
> **как коммитить и деплоить**, и — самое важное — **как поддерживать доки в актуальном состоянии после каждой задачи**.
>
> Snapshot от 2026-08-04. Изменён после post-mortem «бот молчит, чек-ины не доходят»
> (см. §3 «ДВА .env файла» ниже — прочти перед любым изменением webhook URL или env).
> актуальном состоянии после каждой задачи**.

## 0. Если нужны серверные операции (SSH)

Алиас SSH, IP, fingerprint ключа, recovery через Contabo rescue-mode —
в локальном файле **`~/.config/kilo/privichki-bootstrap.md`** (НЕ в репозитории).
Прочти его перед любыми `ssh`/`rsync`/`docker exec` командами. Пароль root там
больше не хранится — вход по ed25519 ключу `~/.ssh/id_ed25519_privichki`
через алиас `ssh privichki-prod`.

## 1. Что это за проект (10 секунд)

**Habit Club** — Telegram Mini App для дисциплины по привычкам. Закрытые клубы
(планка, ранний подъём, чтение), ежедневные видео-кружки, денежные штрафы за
пропуски в общий призовой фонд, сезонные призы. **Платежи = мок на MVP**,
бот не вызывает `bot.send_invoice`.

- **User Mini App:** `https://app.prideclub.fun`
- **Admin Mini App:** `https://admin.prideclub.fun` (owner-only)
- **Backend + Bot webhook:** `https://api.prideclub.fun`
- **pgweb (БД UI):** `https://db.prideclub.fun`

## 2. Маршрут чтения доков (по порядку)

> Каждый файл помечен: **🟢 полностью актуален** / **🟡 в целом ок, могут быть
> мелкие отставания** / **🔴 не читай, устарел**.

| # | Файл | Статус | Зачем |
|---|---|---|---|
| 1 | `docs/02-architecture.md` | 🟢 | Главный файл: стек, контейнеры, домены, схема, потоки. Прочти **полностью**. |
| 2 | `docs/04-code-standards.md` | 🟡 | Правила кода. Примеры могут отставать от реального кода — за реальным стилем иди в `apps/backend/app/services/`. |
| 3 | `docs/06-data-model.md` | 🟡 | Схема таблиц + антифрод + идемпотентность. **Список миграций в §3 — актуальный 000..009** (не 000..004 как в старой версии). |
| 4 | `docs/10-deploy.md` | 🟢 | Runbook: rsync → build → up. Подключение через `ssh privichki-prod` (ed25519 ключ). |
| 5 | `docs/09-prod-readiness.md` | 🟡 | Snapshot бэкенда на 2026-07-22. Перед использованием проверь `git log docs/09-prod-readiness.md` — мог обновиться. |
| 6 | `AGENTS.md` (корень) | 🟢 | Правила поведения агента: layered architecture, DI, деньги в `int`, PII не логировать. |
| 7 | `docs/01-concept.md` | 🟢 | Продуктовая концепция (стабильна с initial commit). Только если задача про продукт/экономику. |
| 8 | `docs/03-project-structure.md` | 🟡 | Общая структура монорепо. **Актуальное дерево кода** (с admin-контуром и worker тасками) — лучше смотри `ls apps/backend/app/api/admin/v1/` напрямую. |
| 9 | `docs/07-security-and-ops.md` | 🟡 | Auth (§2) актуален. **Хостинг (§1) = Contabo Германия, не Selectel РФ**. Бэкапы (§4) и мониторинг (§7) **не развёрнуты**. |
| 10 | `docs/05-ui-ux.md` | 🟡 | Концепция дизайна + палитра. **Экраны** дополнены, но **AI-комендант** не реализован в MVP. |
| 11 | `docs/08-readme.md` | 🟡 | Команды `make ...`. **VPS-секция — целевая, не реальная** (см. 09). |
| 12 | `apps/frontend/docs/STATUS.md` | 🟢 | Специфика фронта: экраны, UI kit, билд, метрики. |

> Не читай `docs/archive/` без явной задачи — это история решений (6 итераций
> ревью), полезно для понимания "почему", но не для текущей работы.

## 3. Где что лежит — локально и на сервере

> Snapshot от 2026-08-13 — добавлено `apps/backend/scripts/e2e/`
> (reusable end-to-end simulator: scenario_happy_path + cleanup).

### Локально (`/Users/dmitriy/Downloads/Privichki`)

```
apps/
├── backend/        # FastAPI 0.115 + SQLAlchemy 2.0 + asyncpg 0.30 + Alembic 1.14
│   ├── app/api/{v1,admin/v1}/
│   ├── app/services/        # CheckinService, PenaltyService, HabitService, PaymentService, …
│   ├── app/repositories/    # HabitRepository, MembershipRepository, …
│   ├── app/core/            # config, security, middleware, exceptions, constants, logging
│   ├── app/db/              # session, redis
│   ├── alembic/versions/    # 000_extensions → 009_chat_id_partial_unique
│   ├── scripts/
│   │   ├── seed_dev_data.py # docker exec backend python -m scripts.seed_dev_data
│   │   ├── register_webhook.py
│   │   └── e2e/             # e2e-simulation (см. apps/backend/scripts/e2e/README.md)
│   │       ├── auth.py       # generate_init_data (HMAC-SHA256), FakeUser, generate_service_token
│   │       ├── core.py       # E2EHttp (initData/service/webhook/internal), E2EDatabase (read-only SQL)
│   │       ├── webhook.py    # фабрики фейковых Telegram Update JSON
│   │       ├── scenario_happy_path.py  # полный путь: create → topup → join → checkin → catch → reject
│   │       └── cleanup.py    # удаление E2E-артефактов (DRY-RUN по умолчанию)
│   └── tests/               # 161 тест
├── bot/            # aiogram 3.30 + aiohttp 3.13 (webhook на :8080)
│   └── bot/handlers/        # start, checkin, payments, chat_member
├── worker/         # Celery 5.4 + Redis, --pool=solo, 8 tasks + 4 cron
│   └── worker/tasks/        # process_checkin, process_penalty, process_payment,
│                            # apply_catch_bonus, close_catch_window, close_season,
│                            # expire_bonus_points, integrity_check_*
└── frontend/       # React 18 + Vite 6 + Tailwind 3 + RQ 5 + Zustand 5
    ├── src/{pages,widgets,shared,admin}/
    │   admin/               # Admin Mini App (отдельный роутер)
    └── docs/STATUS.md       # специфика фронта

packages/shared/security.py  # initData + JWT, копируется во все Python-образы
infra/                        # docker-compose.yml + Dockerfile'ы + nginx/ + backup/ + deploy.sh
docs/                         # 01..10 + README.md + этот файл
AGENTS.md                     # правила для AI-агентов
```

### На сервере (`169.58.52.78`)

```
/app/                                   ← актуальная кодовая база (без .git)
├── apps/{backend,bot,frontend,worker}/ ← те же файлы, что локально
├── packages/shared/security.py
├── infra/                              ← docker-compose.yml + Dockerfile'ы
├── .env                                ← PROD-секреты (chmod 600, монтируется в backend)
├── infra/.env                          ← для docker-compose ${VAR} подстановки (см. ⚠️ ниже)

Docker volumes:
  habit-club_pgdata         ← данные PostgreSQL 16
  habit-club_redisdata      ← Redis 7 (AOF)
  habit-club_club_uploads   ← аплоады; расшарен backend:/app/static + frontend:/usr/share/nginx/html/static

Docker network:
  habit-club_default        ← bridge, 172.18.0.0/16, DNS по именам сервисов

Docker контейнеры (7):
  habit-postgres (172.18.0.2)  postgres:16-alpine
  habit-redis    (172.18.0.3)  redis:7-alpine (AOF, 256MB cap)
  habit-backend  (172.18.0.4)  habit-club-backend (FastAPI, uvicorn ×2)
  habit-pgweb    (172.18.0.5)  sosedoff/pgweb (basic auth)
  habit-frontend (172.18.0.6)  habit-club-frontend (multi-stage nginx:1.27)
  habit-worker   (172.18.0.7)  habit-club-worker (Celery --pool=solo)
  habit-bot      (172.18.0.8)  habit-club-bot (aiogram 3.30 + aiohttp :8080)

Host nginx:
  /etc/nginx/sites-enabled/habit-club   ← reverse proxy 80/443 → 127.0.0.1:{5173,8000,8080,8081}
  /etc/letsencrypt/                      ← TLS по домену
```

### ⚠️ На сервере ДВА `.env` файла — НЕ путай

> **Snapshot 2026-08-04:** главный урок post-mortem «бот молчит, чек-ины не доходят». Запомни это
> перед любым изменением webhook / webapp URL, токенов или env-переменных, которые читает
> `docker-compose`.

| Файл | Кто читает | Что внутри |
|---|---|---|
| `/app/.env` | Backend-контейнер (монтируется как `/app/.env`) + Pydantic Settings в `app/core/config.py` | Секреты для backend: `DATABASE_URL`, `SERVICE_SECRET`, `BOT_TOKEN`, `OWNER_TELEGRAM_ID`, и т.д. |
| `/app/infra/.env` | **Только** `docker-compose` для `${VAR}` интерполяции в `infra/docker-compose.yml` | Переменные, пробрасываемые в контейнеры через `x-backend-env` / `x-bot-env` / `x-worker-env` YAML anchors. **Включает `WEBHOOK_BASE_URL`, `WEBAPP_URL`, `WEBHOOK_SECRET`, `BOT_TOKEN`, `SERVICE_SECRET`, `ENVIRONMENT`** |

**Типичная ошибка нового агента** (snapshot 2026-08-04):

```bash
# ❌ Неправильно — поправил /app/.env, перезапустил бота, ничего не помогло
ssh privichki-prod 'sed -i ... /app/.env && docker compose up -d bot'

# ✅ Правильно — править /app/infra/.env (это читает docker-compose)
ssh privichki-prod 'sed -i ... /app/infra/.env && cd /app/infra && docker compose up -d bot'
```

**Почему так:** `docker-compose` ищет `.env` рядом с `docker-compose.yml` (т.е. в `/app/infra/`)
и подставляет значения `${VAR}` в `environment:` блоках сервисов **на старте compose**.
`/app/.env` — это уже файл **внутри** backend-контейнера (или volume-mounted), его читает только
сам backend, не compose.

**Какие переменные живут ГДЕ (на 2026-08-04):**

| Переменная | Файл | Почему |
|---|---|---|
| `WEBHOOK_BASE_URL` | `/app/infra/.env` | Нужна в `x-bot-env` через `${...}` подстановку compose |
| `WEBAPP_URL` | `/app/infra/.env` | Аналогично |
| `WEBHOOK_SECRET`, `BOT_TOKEN`, `SERVICE_SECRET` | `/app/infra/.env` + `/app/.env` (дубликат) | Compose читает одну, backend читает другую |
| `ENVIRONMENT` | `/app/infra/.env` (читается compose) + backend читает свою копию | Если в compose не пробросить — будет `development` и fail-fast валидация не сработает |
| `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL` | `/app/infra/.env` (compose) + `/app/.env` (backend) | Аналогично |
| `OWNER_TELEGRAM_ID`, `ADMIN_TELEGRAM_CHAT_ID`, `SENTRY_DSN` | `/app/infra/.env` (compose) + `/app/.env` (backend) | Аналогично |

**Защита от регрессии (snapshot 2026-08-04):** в `infra/docker-compose.yml` для обязательных
URL-переменных бота используется синтаксис `${VAR:?must be set in prod ...}` — пустое значение
рвёт `docker compose up` ДО старта контейнера (см. `x-bot-env` anchor, строки `WEBHOOK_BASE_URL`,
`WEBAPP_URL`). Теперь нельзя случайно задеплоить с пустым или битым URL.

**После правки переменных в `/app/infra/.env` — перезапустить ТОЛЬКО соответствующий контейнер:**

```bash
ssh privichki-prod 'cd /app/infra && docker compose up -d bot'      # только бот
ssh privichki-prod 'cd /app/infra && docker compose up -d backend'   # только бэк
ssh privichki-prod 'cd /app/infra && docker compose up -d worker'    # только воркер
# НИКОГДА: docker compose down (гасит весь стек)
# НИКОГДА: docker compose up -d --force-recreate (пересоздаёт ВСЁ)
```

## 4. Активные контейнеры (что для чего)

| Контейнер | Внутр. | Порт хоста | Команда / процесс |
|---|---|---|---|
| `habit-postgres` | 5432 | 127.0.0.1:5432 | `postgres` |
| `habit-redis` | 6379 | 127.0.0.1:6379 | `redis-server --appendonly yes --maxmemory 256mb` |
| `habit-backend` | 8000 | 127.0.0.1:8000 | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers` |
| `habit-frontend` | 80 | 127.0.0.1:5173 | `nginx -g 'daemon off;'` (отдаёт dist) |
| `habit-bot` | 8080 | 127.0.0.1:8080 | `python -m bot.main` (aiohttp webhook) |
| `habit-worker` | — | — | `celery -A worker.celery_app worker --loglevel=INFO --pool=solo` |
| `habit-pgweb` | 8081 | 127.0.0.1:8081 | `pgweb` (UI к БД, basic auth) |

## 5. Внутренние связи

- Telegram → `habit-bot` через webhook (`https://api.prideclub.fun/bot/webhook`)
- `habit-bot` → `habit-backend` через `POST /internal/*` (X-Service-Token JWT, TTL 60s)
- `habit-backend` → worker через Celery `app.services.celery_producer.send_task("...", payload)`
  (Redis broker `redis://redis:6379/1`, result backend `:6379/2`)
- `habit-worker` → `habit-postgres` через asyncpg
- `habit-worker` → `habit-redis` для catch rate-limit, today cache, broker
- `habit-frontend` → `habit-backend` через nginx `https://app.prideclub.fun/api/v1/*`
  (X-Telegram-Init-Data)
- Admin Mini App → `habit-backend` через `https://admin.prideclub.fun/admin/v1/*`
  (X-Telegram-Init-Data + owner-gate по `OWNER_TELEGRAM_ID`)

## 6. Git и коммиты — КРИТИЧНО

- **remote:** `https://github.com/Vegass027/prideclub.git`
- **Автор коммитов ОБЯЗАТЕЛЬНО:**
  ```
  git -c user.name=Vegass -c user.email=dmitriy@vegass.dev ...
  ```
  Без этих флагов коммит уйдёт от `Dim41g / ivanov1331d@gmail.com` (твой
  локальный `~/.gitconfig`) — ломается история авторства.
- **HEAD коммиты (snapshot 2026-08-04):**
  - `main` — `bd9fd76 docs(tz): v3.1 — синхронизация с HEAD bdfd9c9` (НЕ двигается без явного «мерджи в main»)
  - `feature/topic-scoped-checkin` — `a0e8577 docs(ssh): switch from sshpass to ed25519 key` (**51 коммит впереди main**, содержит весь прогресс с 2026-07-22: topic-scoped чек-ины + multi-proof_types + bot pre-filter + lint-zero + SSH-docs итерация)
- **`git push origin main`** — только после явного «ок» пользователя. Не пушить самовольно.
- **Push в `feature/*`** — нормальный рабочий процесс, можно без ОК.
- **SSH-доступ к серверу** — алиас `ssh privichki-prod` (ed25519 ключ `~/.ssh/id_ed25519_privichki`). Детали в §0 и в `~/.config/kilo/privichki-bootstrap.md` (вне репо).
- **GitHub-доступ к репо** — split remote: `git fetch` через `git@github-prideclub:` (SSH-ключ `~/.ssh/id_ed25519_github_privichki`, deploy key в репо, read-only), `git push` через `https://github.com/Vegass027/prideclub.git` (PAT в macOS Keychain). Конфиг — `~/.ssh/config` блок `github-prideclub`.

> ⚠️ **Про «расхождение веток» — нормальный git-flow, не баг.**
>
> `feature/topic-scoped-checkin` живёт как рабочая ветка и **уходит вперёд main**
> на ~десятки коммитов по мере итераций (topic-scoped чек-ины, multi-proof_types,
> bot pre-filter, lint-zero, и т.д.). Это **намеренно**: каждая итерация —
> отдельная фича, отдельная ветка, отдельный push в `origin/feature/*`. В `main`
> ничего не попадает, пока юзер явно не скажет «мерджи в main».
>
> Если ты (новый агент) видишь «X коммитов впереди main» — это не ошибка,
> не «потерянный код», не «незапушенное состояние». Это **нормальное состояние
> рабочей feature-ветки**. Не пытайся «починить» — не мерджи и не пуши в main
> без явного ок юзера. Просто продолжай работу в feature-ветке.
>
> **Как проверить синхронность** трёх точек (локально / origin / сервер) перед
> любой серьёзной задачей:
> ```bash
> # Все три должны давать один хэш:
> git rev-parse HEAD
> git rev-parse origin/feature/topic-scoped-checkin
> ssh root@169.58.52.78 "cd /app/apps/backend && find . -name '*.py' -exec sha256sum {} \;" | sort > /tmp/server.txt
> find apps/backend -name '*.py' -exec sha256sum {} \; | sort > /tmp/local.txt
> diff /tmp/local.txt /tmp/server.txt
> ```
> Если `diff` пустой — всё идентично, можно работать.

## 7. Локальная разработка (команды)

```bash
# Полный стек
make dev              # postgres, redis, backend, bot, worker
make down             # остановить
make logs             # логи всех сервисов
make logs-backend     # только backend

# Тесты и линт
make test             # pytest (backend 161 + worker 34)
make lint             # ruff + mypy
make format           # black + ruff --fix

# Миграции
make migrate          # применить
make migrate-test     # upgrade head → downgrade base → upgrade head
make migrate-new m="описание"

# Только фронт
cd apps/frontend
npm install
npm run dev           # http://localhost:5173
npm run build         # tsc --noEmit && vite build → dist/
```

## 8. Деплой на прод (точная процедура)

```bash
# Локально: тесты, коммит с правильным автором, push
cd /Users/dmitriy/Downloads/Privichki
make test
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev commit -am "..."
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev push origin main

# Только после явного "ок" на деплой:
# 1. Синхронизация через privichki-prod (ed25519 ключ) на хост в стэйджинг.
# ВАЖНО: backend, worker, bot — image-based контейнеры (см. §8.1 ниже).
# Каждый сервис требует СВОЙ rsync + СВОЙ docker compose build.
# Стейджинг /tmp/privichki_new/{backend,worker,bot} должен существовать.
rsync -az --delete apps/backend/ privichki-prod:/tmp/privichki_new/backend/
rsync -az --delete apps/worker/  privichki-prod:/tmp/privichki_new/worker/
rsync -az --delete apps/bot/     privichki-prod:/tmp/privichki_new/bot/

# 2. Применение в /app
ssh privichki-prod 'rsync -az --delete /tmp/privichki_new/backend/ /app/apps/backend/ && rsync -az --delete /tmp/privichki_new/worker/ /app/apps/worker/ && rsync -az --delete /tmp/privichki_new/bot/ /app/apps/bot/'

# 3. Пересборка + рестарт контейнеров (image-based! см. §8.1)
# ВАЖНО: build ПЕРЕД up. Без build up возьмёт старый image со старым кодом.
ssh privichki-prod 'cd /app/infra && docker compose build backend worker bot --no-cache && docker compose up -d backend worker bot'

# 4. Проверка (curl-verify КАЖДОГО слоя отдельно)
ssh privichki-prod 'docker ps --format "{{.Names}}\t{{.Status}}" && curl -s http://127.0.0.1:8000/health && curl -s http://127.0.0.1:8000/ready'
# Для worker и bot проверки — см. §8.2.
```

`ssh privichki-prod` — алиас из `~/.ssh/config`, указывает на `root@169.58.52.78`
через ed25519 ключ `~/.ssh/id_ed25519_privichki` (прописан в `/root/.ssh/authorized_keys`
на сервере). Пароль не используется. Recovery через Contabo rescue-mode
описан в `~/.config/kilo/privichki-bootstrap.md`.

`infra/deploy.sh` обёртка вокруг этой процедуры — можно использовать его.

**НЕ делать** на сервере:
- ❌ `docker compose down` (полностью гасит стек)
- ❌ `docker compose up -d --force-recreate` для всего compose (только для одного сервиса)
- ❌ Прямое редактирование `/app/apps/X/...` (пропадёт при следующем rsync)
- ❌ `rm -rf /app/**`, `docker system prune`, изменения `.env` без ок
- ❌ `docker cp` для правки кода (пропадёт при recreate)

### 8.1 Image-based контейнеры: build после rsync обязателен (2026-08-10)

**Урок из Items 3+4 деплоя (см. коммиты `2012201`, `7343411` deploy):**

Контейнеры **`habit-backend`, `habit-worker`, `habit-bot`** — image-based.
Код копируется в image через `COPY` в Dockerfile (см. `infra/docker/backend.Dockerfile`,
`worker.Dockerfile`, `bot.Dockerfile`). **Bind-mount не настроен** для `/app/apps/`.

Это значит:

- `rsync` обновляет файлы на host'е → контейнер их **НЕ ВИДИТ** (у контейнера свой
  overlay с запечённой копией файлов из Dockerfile).
- `docker compose restart` перезапускает контейнер с **тем же образом** → старый код.
- `docker compose up -d --force-recreate` пересоздаёт контейнер, но **не
  пересобирает image** → всё равно старый код.
- ✅ `docker compose build <service> --no-cache && docker compose up -d <service>`
  — единственный способ доставить новый код.

**Симптом если забыл build:** `python -c "import ..."` в контейнере показывает
старые классы, `grep new_symbol /app/...` в контейнере возвращает 0, но файл на
host'е (в `/app/apps/...`) уже новый. **Тест:** `md5sum /app/apps/<file>` на host
против `docker exec <container> md5sum /app/apps/<file>` внутри контейнера —
должны совпадать; если не совпадают — образ устарел, нужен `build`.

**Только `habit-frontend` — bind-mounted.** Директива `volumes: - /app/apps/frontend/dist:/usr/share/nginx/html`
в `docker-compose.yml` означает что nginx видит свежие файлы после rsync.
**Но** nginx кеширует open file descriptors → нужен `docker exec habit-frontend nginx -s reload`
после rsync (не требует recreate).

**Per-service rsync обязателен.** Staging `/tmp/privichki_new/<service>/` от каждого
сервиса хранится отдельно. Если забыть rsync worker (только backend) — будет
deploy worker из STALE staging (вчерашний код). После очистки `/tmp/` —
staging directories исчезают → rsync молча проваливается → build даёт старый image.
**Защита:** после `rm -rf /tmp/privichki_new` сначала `mkdir -p /tmp/privichki_new/{backend,worker,bot}`,
потом rsync.

### 8.2 curl-verify после деплоя

**Не катай весь стек сразу.** После каждого restart проверяй что не отвалилось:

```bash
# Backend — health + ready + schema check
curl -s http://127.0.0.1:8000/health; echo
curl -s http://127.0.0.1:8000/ready; echo
docker exec habit-backend python -B -c "
from app.core.exceptions import CheckinAlreadyCaughtError  # пример
print('imports work')
"

# Worker — celery inspect active (нет зависших тасок)
docker exec habit-worker celery -A worker.celery_app inspect active | head -5

# Bot — webhook reachable (405 Method Not Allowed = endpoint OK)
curl -sI https://api.prideclub.fun/bot/webhook | head -2
docker exec habit-bot grep new_symbol /app/apps/bot/...  # если файл поменялся

# Frontend — bundle hash + cache headers
curl -s https://app.prideclub.fun/admin/ | grep -o "/assets/admin-[A-Za-z0-9_-]*\\.js"
curl -sI https://app.prideclub.fun/admin/ | grep -i cache-control
```

Между слоями (после backend, до worker, до bot, до frontend) — проверка нужна.
Если что-то отвалилось (например, telegram_bot_api.py aliases), лучше поймать
ДО того как перезапустишь следующий слой (например, bot импортирует из backend'а
через HTTP, и если backend стартовал сломанным — bot не сможет ничего сделать).

### 8.3 Debug-скрипты на проде — НЕ оставлять

При ручной диагностике (smoke test / curl / token generation) часто кладутся
временные скрипты в `/tmp` на проде (например, `gen_token_svc.py`,
`check_task.py`). **Удалять сразу после использования.** Потенциальная
поверхность атаки: если кто-то получит доступ к `/tmp`, скрипт для генерации
service-token (с полным доступом к backend API) лежит готовый.

## 9. Что не работает на проде (snapshot 2026-07-23)

> **Snapshot 2026-08-09:** секция ниже была отредактирована (актуальное состояние
> зафиксировано в Pravki-subscribe-and-join.md §0 и §5 ритуала). Сверяйтесь
> с реальным кодом через `git log --oneline <file>` + `git show <commit>`, а не только
> с этим разделом. Свежие snapshot'ы — `Pravki.md §7`, `docs/09-prod-readiness.md §1.1`.

- 🟡 **Платежи = мок на уровне Telegram API** (НЕ на уровне backend flow). Backend endpoint
  `POST /api/v1/payments/subscribe` работает и создаёт ACTIVE membership с реальной
  записью в `users.deposit_balance` + `transactions(amount=price_month+deposit,
  type=SUBSCRIPTION|DEPOSIT_TOPUP)`. Закрыто коммитом `b98cab0` 2026-08-09: до
  фикса subscription fee попадал на `deposit_balance` (юзеры видели 1250₽ вместо 250₽);
  теперь deposit_balance инкрементируется **только** на `deposit_amount_kopecks`,
  subscription_fee идёт в `transactions.amount` как доход клуба/платформы.
  Что осталось моком: **отсутствие реального `bot.send_invoice`** — на проде нет
  `PROVIDER_TOKEN`, бот не вызывает Telegram Payment API, деньги «списываются»
  без взаимодействия с Telegram. Это отдельная задача (см. Pravki-subscribe-and-join.md §6).
- ✅ **Pravki §Z-22: prefilter holes 5-round fix** (закрыто 2026-08-12,
  серия `b4f8974` → `2eda062` → `3e7f1ee` → `4272cfa` → `92a05be` → `6cf22f2` → `b4cc923`) —
  foundation `CheckinRejectCode` enum с drift-test, закрыты 4 дырки
  (checkin_window_closed / not_checkin_topic с DRIFT FIX мёртвого magic-string /
  membership_paused + membership_left сплит / forwarded с особенностью `forward_date`
  только в aiogram Message). Three-tier защита: bot prefilter sync + backend defense-in-depth
  в `enqueue_checkin` + frontend mapper в `checkinReject.ts`. Canonical priority v2
  по специфичности copy: state-of-day выше time/location выше proof validation.
  Combo-тесты для `caught_today + paused/left/window_closed` зафиксированы.
  Закрыт оригинальный дырявый паттерн "бот отвечает Принято синхронно, worker
  отвергает async" — раньше юзер получал ложный acknowledge. Закрыт баг
  "мини-апп показывает raw-code через SSE" — теперь mapper переводит в
  human-readable. Подробности — `docs/04-code-standards.md §7.1`,
  `docs/06-data-model.md §4.0`, `docs/09-prod-readiness.md §1.1`.
- 🟡 **Известное несоответствие: бот и мини-апп показывают РАЗНЫЕ формулировки
  для `caught_today` (cron `apply_window_expired` vs `apply_catch`).** Worker SSE payload
  `{reason, message}` НЕ содержит `checkin_status` — фронт не может отличить
  "поймали" от "штраф списан, никто не поймал". Бот различает через `state.checkin_status`.
  Финансово одинаково, копия разная. Подробности — `docs/09-prod-readiness.md §3`.
  Отдельный PR для расширения `_publish_checkin_rejected` + mapper.
- ❌ **Бэкапы не развёрнуты.** `backup_cron.sh` готов, нет `aws` CLI, нет
  `S3_*` env, нет cron-задачи. Текущая защита — только volume `habit-club_pgdata`.
- ❌ **Sentry = no-op** (DSN пуст). Grafana не развёрнута. Кастомных Prometheus-метрик нет.
- ❌ **Хостинг — Contabo (Германия), не Selectel (РФ).** ФЗ-152 под риском для
  реальных ПДн (сейчас в БД 2 тест-юзера, 3 habits, 3 memberships, 4 transactions).
- ❌ **AI-комендант и "Удалить аккаунт"** — не реализованы (в v2).
- 🟡 **В БД 0 habits при 9 файлах в `uploads/club_photos/`** — POST
  `/admin/v1/habits` не отрабатывал, расследовать.
  ⚠️ **Расхождение с реальностью:** на 2026-08-09 в `habits` 3 строки (восстановлены
  из pg_dump'а 2026-08-09). Расследование нужно проверить заново.
- 🟡 **Контракт `chat_id` vs `habit_id`** в `apps/backend/app/api/v1/internal_payments.py`
  ожидает `chat_id: int`, бот шлёт `habit_id: str`. 422 без починки.
  ⚠️ **Расхождение:** см. комментарий в `Pravki-subscribe-and-join.md §0` «Текущее
  состояние бота на проде» — `internal_payments.py` остаётся мёртвым кодом (бот не
  вызывает его). Удалить или переименовать параметр — отдельная задача.
- 🟡 **Bot логирует plain text**, не structlog-JSON как backend/worker.
- ✅ **Webhook SSL error (закрыто 2026-08-04)** — `WEBHOOK_BASE_URL` указывал на сырой IP
  в `/app/infra/.env`, Telegram не доставлял апдейты (2 шт в `pending_update_count`).
  Пофикшено: правка `/app/infra/.env` + `docker compose up -d bot`. Детали — §3 «ДВА
  .env файла» + `docs/02-architecture.md` §9.2.
- ✅ **Worker `CheckinService` DI-bug (закрыто 2026-08-04)** — worker не передавал
  `penalty_repo` в конструктор `CheckinService`, чек-ины возвращали `ok=False` без записи.
  Пофикшено: добавлен `penalty_repo=PenaltyRepository(session)` в
  `apps/worker/worker/tasks/process_checkin.py`.
- ✅ **MembershipService LEFT/PAUSED bypass** (закрыто 2026-08-09 коммитом `ae6bd07`,
  сейчас на `feature/fix/left-paused-deposit-bypass` ветке или уже в `main`).
  `join` теперь проверяет deposit ВСЕГДА — никаких исключений для LEFT/PAUSED.
- ⚠️ **Docker build cache конфликт** (2026-08-09, НЕ закрыто) — `docker compose build
  frontend` падает с overlay-конфликтом на `@tanstack/react-query`. Workaround
  применён (commit `4a390e1`): `image: nginx:1.27-alpine` + volume mount на bundle.
  Диагностика первопричины — отдельная задача (см. `docs/10-deploy.md` §runbook
  после фикса этого snapshot'а).
  > **Snapshot 2026-08-13 (Pravki-static):** workaround `4a390e1` создал побочный баг —
  > bind-mount `/app/apps/frontend/dist → /usr/share/nginx/html` перекрыл volume
  > `club_uploads → /usr/share/nginx/html/static`, и frontend-nginx перестал видеть
  > фото/гифки клубов (`GET /static/uploads/club_photos/*.gif → 404` на
  > `app.prideclub.fun`). **Симптом закрыт** (commit `89e6bfe` на
  > `feature/paused-member-ux`): добавлен `location ^~ /static/ { proxy_pass
  > http://habit_backend; }` в `infra/nginx/nginx.prideclub.conf` для
  > `app.prideclub.fun`, `prideclub.fun`, `admin.prideclub.fun` — backend через
  > FastAPI StaticFiles уже корректно отдаёт файлы из `club_uploads` volume
  > (`apps/backend/app/main.py:166`). **Первопричина не закрыта** — нужно
  > вернуть `build:` для `frontend` (см. отдельную задачу).
- ⚠️ **Alembic upgrade через compose не выполняет ALTER TYPE ADD VALUE**
  (2026-08-09, НЕ закрыто). `docker compose run --rm backend alembic upgrade head`
  стартует контейнер, но не пишет в БД — миграция висит. Workaround: ручной
  `docker exec habit-postgres psql` с `ALTER TYPE checkin_status ADD VALUE IF NOT
  EXISTS 'joined_late'` + `UPDATE alembic_version SET version_num='015_...'`.
  Диагностика первопричины — отдельная задача (см. `docs/10-deploy.md` §runbook).

Подробнее — `docs/09-prod-readiness.md` §1.1, `docs/02-architecture.md` §9, `docs/10-deploy.md` §runbook,
и финальные snapshot'ы планов: `Pravki-subscribe-and-join.md` §0 (Z-13..Z-18 + Z-19),
`Pravki-deposit-sse.md` §Z-2.9 (PR #1).

## 9.1. Telegram Mini App кэш на мобильном (first-aid: @WebpageBot)

> Snapshot 2026-08-10. Закрыто через `@WebpageBot` после ловушки с
> admin.prideclub.fun → user app на iOS/Android.

**Симптом (паттерн):** правка фронта/маршрутизации выкатывается на прод,
на **десктопе работает корректно** (Telegram Desktop / Chrome / Safari),
на **мобильном Telegram** (iOS/Android) — старая версия SPA, или
неправильный shell (user вместо admin), или `invalid_init_data` несмотря
на то что curl на сервере возвращает правильный HTML.

**Root cause:** Telegram mobile WebView держит **отдельный кэш** на
URL-базисе, **отдельно от system browser cache**. Этот кэш:

- ❌ НЕ чистится через `Settings → Data and Storage → Clear Cache` в
  Telegram (это только media cache).
- ❌ НЕ чистится через `Sign out → Sign in` (cache привязан к
  installation ID, не к user session).
- ❌ НЕ чистится через iOS `Offload App` (Offload сохраняет
  `Library/Caches` где живёт WebView cache). Нужен **полный Delete App**.
- ❌ Не реагирует на `Cache-Control: no-cache` от сервера для **уже
  закешированных** ответов — директива влияет только на последующие
  запросы после её установки.
- ✅ Чистится через `Settings → Apps → Telegram → Storage → Clear Cache`
  на Android (полностью, включая WebView).
- ✅ Чистится через полный **Delete App + reinstall** на iOS.
- ✅ Чистится через бота `@WebpageBot` — открыть в Telegram, отправить
  URL Mini App (`https://admin.prideclub.fun/`), он отдаст свежий ответ
  в обход WebView-кэша и force-refresh'нет его.

**Алгоритм диагностики (если «на десктопе ок, на мобильном нет»):**

1. `curl https://<domain>/` с сервера — что отдаётся? Если правильный
   shell → сервер ок, проблема в WebView-кэше мобильного клиента.
2. Открыть тот же URL в Safari/Chrome на телефоне (НЕ в Telegram).
   Правильный shell → 100% подтверждение WebView-кэша.
3. Применить `@WebpageBot` (быстрее всего): открыть бота → отправить
   URL → он отдаст свежий ответ и force-refresh'нет WebView-кэш.
4. Если `@WebpageBot` недоступен: iOS — `Settings → General → iPhone
   Storage → Telegram → Delete App → reinstall`; Android — `Settings
   → Apps → Telegram → Storage → Clear Cache`.

**Защита на сервере (применена в `infra/nginx/frontend.nginx.conf`,
коммит `65160c1` 2026-08-10):** `Cache-Control: no-store, no-cache,
must-revalidate` на HTML-шеллах и SPA fallback. Bundle-файлы
(`/assets/`) оставлены с `immutable, max-age=1y` (там content-hash).
Не защищает от уже-закешированного, но предотвращает рецидив при
следующем изменении маршрутизации SPA-shell'ов.

**Defense-in-depth:** при ЛЮБОЙ правке `infra/nginx/frontend.nginx.conf`,
затрагивающей SPA-shell маршрутизацию (`admin.html` ↔ `index.html`),
предупреждать пользователя заранее: «после деплоя admin Mini App на
мобильных может залипнуть кэш → @WebpageBot или Delete+Reinstall».

## 10. Стиль кода — короткая шпаргалка

(Полный список — `docs/04-code-standards.md` и `AGENTS.md`.)

- **Backend:** `api → services → repositories → models`. Сервисы **НЕ** вызывают
  `session.commit()` (исключение — admin endpoint `/admin/v1/habits` помечен комментарием).
- **Деньги — `int` копейки.** Никогда `float`/`Decimal` для `deposit_balance`,
  `penalty_amount`, `prize_pool`, `amount`. Грепнуть `rg "Decimal\\(|float\\("` перед merge.
- **`user_id`** — только из `request.state.telegram_user` (после initData-валидации).
  Никогда параметром запроса.
- **Async I/O** везде. `aiohttp` для HTTP, `asyncpg` для БД, `asyncio.sleep`,
  `asyncio.to_thread` для CPU.
- **DI через конструктор.** Никаких глобальных мутабельных состояний.
- **Celery `send_task` по имени** (см. `apps/backend/app/services/celery_producer.py`).
  Backend НЕ импортирует worker-модули (`include=[]`).
- **Доменные исключения** в `core/exceptions.py`, глобальный handler в `main.py`,
  никаких `try/except Exception` в роутах.
- **Frontend:** данные через хуки над `shared/api` (`useQuery`/`useMutation`).
  Никакого `fetch`/`axios` в компонентах. `any` в TypeScript — только с обоснованием.
- **PII:** НЕ логировать `first_name`, `username`. Только `user_id` (числовой).

## 11. Definition of Done (для каждого изменения)

- [ ] `make test` проходит (161 backend + 34 worker)
- [ ] `make lint` проходит
- [ ] `make migrate-test` проходит (если менялась схема)
- [ ] Нет `float`/`Decimal` для денег (грепнуть diff)
- [ ] Middleware не обойден в новых эндпоинтах (auth через `request.state.telegram_user`)
- [ ] Нет PII в логах
- [ ] Логи + метрики на критических операциях (`duration_ms`)
- [ ] Если менялась документация — соответствующий `docs/*.md` обновлён **тем же коммитом**
      или отдельным коммитом `docs: ...` (см. §12)

## 12. Ритуал поддержания доков в актуальном состоянии

> **Главное правило проекта.** Документы устаревают, если их не обновлять вместе
> с кодом. После каждой успешно выполненной задачи, **когда пользователь говорит
> "всё работает"**, агент ОБЯЗАН пройти этот чек-лист.

**Алгоритм "Всё работает → доки обновлены":**

1. **Определи**, какие файлы в `docs/`, `AGENTS.md`, `apps/frontend/docs/STATUS.md`
   относятся к сделанному изменению. Минимум — какой раздел какой док-страницы
   затрагивается. Подсказки:
   - Менял backend API → `docs/04`, `docs/06`, `docs/09`
   - Менял frontend экраны → `apps/frontend/docs/STATUS.md`, `docs/05`
   - Менял Celery таску → `docs/02` §2 "Очереди", `docs/03` §5, `docs/04` §9
   - Менял auth → `docs/07` §2, `AGENTS.md`
   - Менял docker-compose / Dockerfile → `docs/02` §2-3, `docs/03` §6, `docs/10`
   - Менял БД-миграцию → `docs/06` §3 (список миграций)
   - Менял nginx/домен → `docs/02` §3, `docs/10` §3
   - Любое серверное изменение → `docs/02` §9 "Известные проблемы" (если починил
     что-то из списка — удалить оттуда)

2. **Сверь** каждый из этих файлов с реальным кодом:
   - Версии пакетов (`requirements.txt`, `package.json`)
   - Имена файлов/тасок/функций
   - Схема таблиц (`apps/backend/app/models/`)
   - Список эндпоинтов (`apps/backend/app/api/`)
   - Текущее состояние БД (`SELECT count(*) FROM ...`)

3. **Если расхождения есть** — внеси точечные правки в те же файлы. Формат правок:
   - Snapshot-метка вверху файла (`> Snapshot от YYYY-MM-DD`)
   - Точечный edit, не переписывание всего файла
   - Если факт в файле был неверный — пиши `⚠️ Расхождение с реальностью` + правда
   - Если раздел целиком неактуален — пометь `> Этот раздел в плане, не реализовано`
     (как сделано в `docs/08` для VPS-секции)

4. **Если расхождений нет** — коммить без правок доков.

5. **Коммить и пушить:**
   - Один коммит = одно изменение. Не мешай code + docs в один коммит, если
     можно разделить.
   - Формат: `feat/fix/refactor(scope): ...` для кода, `docs: ...` для документации.
   - Автор: `git -c user.name=Vegass -c user.email=dmitriy@vegass.dev ...`
   - Push только после явного "ок" пользователя.

6. **Сообщи пользователю** что именно обновил. Формат:
   ```
   Что обновил: [список файлов и что в них поменялось]
   ```
   Если ничего не поменялось в доках — сообщи почему (проверил, расхождений нет).

**Анти-паттерны:**
- ❌ Говорить "доки надо обновить, но я не буду" — это нарушение ритуала.
- ❌ Менять код и говорить "всё работает" без проверки доков.
- ❌ Делать правки доков в коммите с feature-изменениями (мешает blame).
- ❌ Говорить "это не моя задача" — поддержание доков в актуальном состоянии это
  часть задачи.

## 13. Чего НЕ делать

- ❌ Не коммитить секреты, пароли, `.env`, IP с кредами.
- ❌ Не коммитить **приватные SSH-ключи** (`id_ed25519_*` без `.pub`, `*.pem`,
  сертификаты). Публичные ключи (`.pub`) безопасны для документов.
- ❌ Не править `/app` на сервере напрямую.
- ❌ Не использовать `docker compose down` без ок.
- ❌ Не коммитить от `Dim41g / ivanov1331d@gmail.com` (твой локальный git, **не** репо-автор).
- ❌ Не пушить в `origin/main` без явной команды "пуш".
- ❌ Не использовать `any` в TypeScript без обоснования.
- ❌ Не логировать `first_name` / `username` (PII).
- ❌ Не делать "быстрых" изменений на сервере по SSH без плана в чате.
- ❌ Не переписывать документацию целиком — точечные правки.
- ❌ Не добавлять бизнес-логику в роуты/хендлеры (только в сервисах).

## 14. Версии пакетов (для справки)

| Компонент | Версия |
|---|---|
| Python | 3.12 |
| FastAPI | 0.115.5 |
| SQLAlchemy | 2.0.36 |
| asyncpg | 0.30.0 |
| Alembic | 1.14.0 |
| Pydantic | 2.10.3 |
| structlog | 24.4.0 |
| aiogram | 3.30.0 |
| aiohttp (bot) | 3.13.5 |
| Celery | 5.4.0 |
| Redis | 5.2.1 |
| Node | 20-alpine (build), nginx 1.27-alpine (runtime) |
| Vite | 6 |
| React | 18 |
| TailwindCSS | 3 |
| React Query | 5 |
| Zustand | 5 |
| React Router | 6 |
| @telegram-apps/sdk | 3.3 |
| PostgreSQL | 16 |
| Redis (сервер) | 7 |

## 15. Сводный стек (одним абзацем)

React 18 + TypeScript 5 + Vite 6 → multi-stage Docker (nginx 1.27) — фронт
(user + admin Mini Apps), общаются с FastAPI 0.115 (Python 3.12, SQLAlchemy 2.0,
asyncpg, Pydantic 2.10) через `/api/v1/*` (initData JWT) и `/admin/v1/*` (initData
+ owner-gate), плюс `/internal/*` (service-token JWT) для бота и воркера. Bot =
aiogram 3.30 + aiohttp 3.13 webhook на :8080, лёгкие POST'ы на backend.
Worker = Celery 5.4 (--pool=solo, async внутри), 8 ad-hoc тасок + 4 cron-таски
(close_catch_window, expire_bonus_points, integrity_check, close_season),
обрабатывает задачи, положенные backend'ом через `send_task` по имени. БД =
PostgreSQL 16 (9 миграций, 16 таблиц). Кэш + очереди = Redis 7 (AOF, 256MB cap).
Всё в Docker Compose, на одной bridge-сети `habit-club_default`. Сервер — Contabo
VPS 4 (Германия, не РФ), nginx на хосте как reverse proxy. Деплой — rsync +
`docker compose build` + `up -d`. Деньги — `int` копейки. Auth — двухконтурная
(initData + service-token JWT с aud/iss/exp). Платежи = мок на фронте.
## 11. Серия фиксов §Z-21 (2026-08-10, snapshot)

> **Snapshot 2026-08-10.** Закрытая серия из 9 items + docs. Все коммиты в `feat/subscribe-and-join`.

### Карта серии (все задеплоены на проде)

| Item | Что | Commit |
|---|---|---|
| 1+2 | `can_catch` в /members видит Penalty + `Checkin(status)` при Penalty | `106734b` |
| 3 | StatusBadge «Пойман» 🎯 + TodayPage caught-секция | `af9b22a` |
| 4 | Бот + воркер отклоняют повторный чек-ин после поимки | `f429f80` |
| 5 | `checkin_window_closed` с реальным временем окна | `be6702d` |
| 6 | `publish_to_habit` + worker task `publish_catch_event` (Variant Б) | `175c3ff` |
| 7 | SSE multiplex с двумя cursor'ами | `527f67e` |
| 8 | catch + you_were_caught broadcasts (HTTP catch_violator only) | `cb5938d` |
| 9 | `useHabitSse` hook (frontend) | `d94fc91` |
| Docs | AGENT_BOOTSTRAP §8.1+§8.2+§8.3 (image-based deploy уроки) | `3a4d936` |
| Infra | telegram_bot_api алиасы + http_factory restore (compat с stash) | `0d6855b`, `0087d15` |

### Runtime user-flow (после Items 1-9)

1. **Юзер открывает клуб** → `GET /habits/{id}/today` → рендер. `useHabitSse`
   открывает multiplex SSE-стрим (`?last_event_id=$&last_event_id_habit=$`).

2. **Юзер делает чек-ин** → бот pre-filter → `POST /internal/checkins/process`
   → defense-in-depth (`CheckinJoinedLateError` / `CheckinAlreadyCaughtError`)
   → worker `process_checkin` → XADD в `sse:user:{u}:{h}` → SSE-event
   `checkin.accepted` → frontend `setQueryData(["today", habitId], payload)`.

3. **Юзер поймал кого-то** (кнопка «Поймать») → backend `apply_catch` →
   **ДВА `send_task` в раздельных `try/except`** (Item 8):
   - `publish_catch_event` → worker XADD в `sse:habit:{h}` (broadcast — все
     участники получают `catch` event).
   - `publish_you_were_caught` → worker XADD в `sse:user:{violator}:{h}`
     (personal для жертвы).
   На frontend: multiplex EventSource доставляет оба event'а. `invalidateQueries`
   для members/today/wallet/balance, haptic medium (catch) + haptic warning
   (you_were_caught).

4. **Cron `close_catch_window`** → `penalty_service.apply_window_expired`
   пишет `Penalty(reason=WINDOW_CLOSED_NO_CATCH)` + `Checkin(status='missed')`.
   UI пропустивших обновляется только при следующем mount через `staleTime`
   / `refetch` (**НЕ через realtime SSE — осознанное ограничение Item 8**).
   В cron-сценарии нет `catcher_user_id`/`catcher_first_name` для payload —
   если broadcast нужен, потребуется отдельный Item 10.

5. **Юзер пытается повторно чек-инуть после поимки** → бот pre-filter ловит
   `caught_today` → REJECT_CAUGHT_TODAY (текст разный для apply_catch vs cron).
   Defense-in-depth на backend: `CheckinAlreadyCaughtError`.

### Архитектурные решения (Items 6-9)

- **Variant Б (Item 6):** Backend → Celery task → Worker `EventPublisher`.
  Backend НЕ знает про Redis Streams / async Redis. `include=[]` —
  backend не импортирует worker tasks. +0 строк shared кода.
- **Multiplex SSE (Item 7):** Один `EventSource` читает оба стрима
  (`sse:user:{u}:{h}` + `sse:habit:{h}`) с per-stream cursor'ами.
  Backend XREAD STREAMS multiplexes в одном round-trip. Frontend multiplexes
  в одном EventSource.
- **Variant 1 backward-compat (Item 7):** Legacy v1 клиент (без
  `last_event_id_habit`) → только user-stream (habit-stream физически не
  подписан). Drift detection warning (`sse_multiplex_drift_detected`) в
  backend для диагностики frontend-bug'а.
- **COLLISION-изоляция (Item 6 + Item 8):** `idempotency_key(..., event_type='caught')`
  → `sse_published:caught:{m}:{d}` — независимый namespace от
  `sse_published:checkin:{m}:{d}`. Утренний `checkin.rejected` не блокирует
  вечерний `you_were_caught`.
- **SEPARATE try/except (Item 8):** Два `send_task` в catch_violator обёрнуты
  в раздельные try/except — если broker упал на первом, второй всё равно
  вызывается. Тест `test_catch_handler_two_send_tasks_independent_try_except`
  покрывает через `side_effect=[Exception, None]`.
- **Wallet invalidate (Q2 разведки):** Frontend инвалидирует wallet в обоих
  event-listener'ах (`catch` и `you_were_caught`). React Query dedup'ит через
  `notifyManager.batch` + `setTimeout(0)` — безопасно (исследовано из
  исходников, не предположение).

### Тестовый baseline

| Слой | Passed | Pre-existing failed | Замечание |
|---|---|---|---|
| Backend | 364 | 11 | admin_habits_api (нужен Redis), user_photo_endpoint (mock AvatarService) |
| Worker | 77 | 9 | test_process_penalty и др. |
| Bot | 40 | 0 | все зелёные |
| Frontend | 48 | 0 | все зелёные |

**20 pre-existing фейлов — baseline, не регрессии серии.** Подтверждено через
`git stash` проверки до/после каждого Item.

### Известные ограничения (НЕ в серии §Z-21)

- **Cron `apply_window_expired` НЕ транслирует SSE broadcast** — осознанное
  ограничение Item 8 (нет catcher'а в payload). При необходимости — Item 10.
- **Платежи = мок** (backend flow есть, но bot не вызывает `send_invoice`).
- **Sentry/Grafana/Prometheus** не задеплоены (см. §9 AGENT_BOOTSTRAP).
- **`_b` legacy debug-файлы в `/tmp/`** — не чистил, не моя задача.
- **2 user_photo_endpoint fail + 9 admin_habits_api fail** — pre-existing.
