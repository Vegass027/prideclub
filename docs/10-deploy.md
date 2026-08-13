# DEPLOY — операционный runbook

> Прод-сервер: Contabo Cloud VPS 4 (4 vCPU / 8 GB / 100 GB SSD), `169.58.52.78`, Ubuntu 24.04.
> Документ описывает ТОЛЬКО операции деплоя и мониторинга. Бизнес-логика в `docs/01–07`.

## 1. Подключение

**SSH-доступ — по ed25519 ключу через алиас `privichki-prod`** (настроен в
`~/.ssh/config` на маке, ключ `~/.ssh/id_ed25519_privichki` прописан в
`/root/.ssh/authorized_keys` на сервере). Пароль не требуется и не используется.

```bash
ssh privichki-prod                                # shell
ssh privichki-prod 'docker ps'                    # удалённая команда
rsync -az apps/backend/ privichki-prod:/tmp/new/  # rsync через ssh-config alias
```

Пароль (если задан на сервере через `passwd root`) нужен ТОЛЬКО для аварийного
recovery в Contabo rescue-mode — см. `~/.config/kilo/privichki-bootstrap.md`.

## 2. Структура на сервере

```
/app/                     # монтирован из infra/docker-compose.yml
  ├── apps/
  │   ├── backend/        # FastAPI (habit-backend контейнер)
  │   ├── worker/         # Celery worker + beat (habit-worker контейнер)
  │   ├── bot/            # aiogram webhook (habit-bot контейнер)
  │   ├── frontend/       # Vite build (habit-frontend контейнер, :5173)
  │   └── packages/shared/
  ├── infra/docker-compose.yml
  ├── .env                # PROD-секреты, chmod 600
  └── infra/postgres/, infra/nginx/, infra/backup/

/tmp/privichki_new/       # staging-копия перед rsync в /app/apps/
```

## 3. Процедура деплоя (одного изменения)

### 3.1 Локально: код + тесты

```bash
# Локально
cd apps/backend
.venv/bin/python -m pytest tests/ -x -q          # должно быть 55 passed
cd ../worker
PYTHONPATH=/Users/dmitriy/Downloads/Privichki/apps/backend \
  /Users/dmitriy/Downloads/Privichki/apps/backend/.venv/bin/python \
  -m pytest tests/ -x -q                          # 32 passed

# Закоммитить + запушить
cd /Users/dmitriy/Downloads/Privichki
git add -A && git commit -m "..."
git push origin main
```

### 3.2 На сервере: rsync + пересобрать контейнеры

```bash
ssh privichki-prod '
mkdir -p /tmp/privichki_new
'  # стэйджинг чистится при рестарте контейнера

# Локально (быстрее — отправляем только изменённые файлы)
rsync -az apps/backend/app/services/http_rate_limiter.py \
  privichki-prod:/tmp/privichki_new/backend/app/services/

# ИЛИ полный sync (медленно но надёжно)
rsync -az --delete apps/backend/ privichki-prod:/tmp/privichki_new/backend/

# Применяем rsync в /app
ssh privichki-prod '
rsync -az --delete /tmp/privichki_new/backend/ /app/apps/backend/
rsync -az --delete /tmp/privichki_new/worker/ /app/apps/worker/
'
```

### 3.3 Пересобрать и рестартовать контейнеры

**Изменения только в backend-коде (services, api, schemas):**
```bash
ssh privichki-prod '
cd /app/infra
docker compose build backend --no-cache
docker compose up -d backend
'
```

**Изменения в worker/celery_app.py или импортах backend:**
```bash
ssh privichki-prod '
cd /app/infra
docker compose build worker --no-cache
docker compose up -d worker
'
```

> ⚠️ Worker image копирует backend код через `COPY apps/backend/app /app/app` при build.
> `docker cp` НЕ сохраняется между recreate — всегда `build --no-cache`.

**Изменения в requirements.txt:**
```bash
ssh privichki-prod '
cd /app/infra
docker compose build --no-cache          # пересобрать ВСЕ контейнеры
docker compose up -d
'
```

### 3.4 Проверка после деплоя

```bash
ssh privichki-prod '
docker ps --format "table {{.Names}}\t{{.Status}}"
curl -s -m 5 http://127.0.0.1:8000/health     # {"status":"ok"}
curl -s -m 5 http://127.0.0.1:8000/ready      # {"status":"ready"}
docker logs habit-backend --tail 5
docker logs habit-worker --tail 5
'
```

## 4. Аварийные ситуации

### 4.1 Backend не стартует

```bash
ssh privichki-prod '
docker logs habit-backend --tail 50
'  # обычно: ImportError (не скопирован новый модуль) → повторить rsync + rebuild
```

### 4.2 Worker перестал обрабатывать задачи

```bash
ssh privichki-prod '
docker logs habit-worker --tail 30
docker exec habit-redis redis-cli LLEN celery  # длина очереди
'  # если очередь растёт — worker упал, рестарт: docker compose restart worker
```

### 4.3 Postgres connection full

```bash
ssh privichki-prod '
docker exec habit-postgres psql -U habits -d habits \
  -c "SELECT count(*) FROM pg_stat_activity;"
docker exec habit-postgres psql -U habits -d habits \
  -c "SELECT pid, state, query_start, query FROM pg_stat_activity WHERE state != '\''idle'\'' ORDER BY query_start;"
'
```

### 4.4 Откат к предыдущему коммиту

```bash
ssh privichki-prod '
cd /app/apps/backend
git log --oneline -5          # посмотреть
git checkout <prev_commit>    # откатить код
'
# Пересобрать образы (если менялись requirements.txt)
cd /app/infra
docker compose build --no-cache
docker compose up -d
```

## 5. Мониторинг

### 5.1 Метрики

```bash
curl -s http://127.0.0.1:8000/metrics | grep habit_
```

Сейчас на сервере `/metrics` отдаёт дефолтные метрики Python + процесса. Кастомных пока нет — задача v2.

### 5.2 Логи (JSON)

```bash
docker logs habit-backend --tail 50 -f | jq .    # JSON формат
docker logs habit-worker --tail 50 -f | jq .     # JSON формат (после c22fb6c)
```

Структура JSON: `event`, `level`, `timestamp`, `extra.*` (request_id, user_id, path, status, duration_ms).

## 6. Тесты

### 6.1 Локально

```bash
# backend
cd apps/backend
.venv/bin/python -m pytest tests/ -x -q
# 55 passed

# worker
cd apps/worker
PYTHONPATH=/Users/dmitriy/Downloads/Privichki/apps/backend:$PYTHONPATH \
  /Users/dmitriy/Downloads/Privichki/apps/backend/.venv/bin/python \
  -m pytest tests/ -x -q
# 32 passed
```

### 6.2 На сервере

```bash
ssh privichki-prod '
cd /app/apps/backend
DATABASE_URL=postgresql+asyncpg://habits:habits@postgres:5432/habits \
REDIS_URL=redis://redis:6379/0 \
  docker run --rm --network habit-club_backend \
  -v /app/apps/backend:/app -w /app \
  -e DATABASE_URL=postgresql+asyncpg://habits:habits@postgres:5432/habits \
  -e REDIS_URL=redis://redis:6379/0 \
  habit-club-backend \
  pytest -v
'
```

Или проще (без docker run):
```bash
ssh privichki-prod '
cd /app/apps/backend
find . -type d -name __pycache__ -exec rm -rf {} +
DATABASE_URL=postgresql+asyncpg://habits:habits@127.0.0.1:5432/habits \
REDIS_URL=redis://127.0.0.1:6379/0 \
  /app/apps/backend/.venv/bin/python -m pytest tests/ -v
'
```

## 7. Бэкапы (TODO)

`infra/backup/backup_cron.sh` готов, но на сервере:
- нет `aws` CLI (нужно установить или использовать `mc`/`rclone`)
- нет `S3_*` env-переменных (Selectel/Yandex S3)
- нет cron-задачи

Когда будут S3 credentials:
```bash
# Поставить awscli
apt-get install -y awscli

# Закинуть в cron (ежедневно в 3:00 UTC)
echo "0 3 * * * /app/infra/backup/backup_cron.sh" >> /etc/cron.d/habit-backup

# Тест
/app/infra/backup/backup_cron.sh
```

## 8. Известные ограничения / что НЕ делается руками

- ❌ Не редактировать код в `/app/apps/*` напрямую на сервере — пропадёт при следующем rsync.
- ❌ Не использовать `docker cp` для изменения кода — пропадёт при recreate.
- ❌ Не коммитить в main без проверки CI (GitHub Actions).
- ❌ Не запускать тесты на прод-БД напрямую (только в test-DB через миграцию test).

## 9. Известные инфраструктурные баги (snapshot 2026-08-09)

Два бага infra-уровня, обнаружены при deploy Pravki-subscribe-and-join. Workaround'ы
задокументированы, диагностика первопричин — отдельные задачи.

### 9.1 Docker build cache overlay-конфликт (frontend)

**Симптом:** при попытке `docker compose build frontend` (или любой попытке
пересобрать образ `habit-club-frontend`) — падает с ошибкой:

```
failed to solve: cannot replace to directory
/var/lib/docker/overlay2/.../merged/app/node_modules/@tanstack/react-query
with file
```

**Причина:** на сервере в Docker overlay cache остались слои от прошлого
multi-stage build, где `@tanstack/react-query` был directory (от `npm ci`).
Новый build context добавляет этот путь как file (после смены lockfile/package.json).
Overlay filesystem не позволяет «directory → file» замещение в одном слое.

**Что НЕ помогло:**
- `docker compose build --no-cache` (overlay cache не очищается).
- `docker compose build --no-cache --pull` (тот же overlay-конфликт).
- `DOCKER_BUILDKIT=1 BUILDKIT_SANDBOX_HOSTNAME=alt-build` (тот же overlay-конфликт).
- `docker rmi habit-club-frontend` (overlay остаётся).
- `docker builder prune -af` (не очистил проблемные слои).

**Workaround (применён 2026-08-09, commit `4a390e1`):**
в `infra/docker-compose.yml` для сервиса `frontend` убран `build:` и используется
базовый `image: nginx:1.27-alpine` с volume mount на bundle:

```yaml
  frontend:
    image: nginx:1.27-alpine   # было build: {context: .., dockerfile: ...}
    container_name: habit-frontend
    volumes:
      # Bundle живёт на хосте, rsync'ится при deploy.
      - /app/apps/frontend/dist:/usr/share/nginx/html
      - club_uploads:/usr/share/nginx/html/static
    ports:
      - "127.0.0.1:5173:80"
```

> **Побочный эффект workaround'а (2026-08-13, Pravki-static):** bind-mount
> `/app/apps/frontend/dist → /usr/share/nginx/html` перекрывает volume
> `club_uploads → /usr/share/nginx/html/static` (volume монтируется
> «в пустоту», т.к. в dist/ нет подпапки `static`). В результате frontend-nginx
> возвращает 404 на `GET /static/uploads/club_photos/*` (фото/гифки клубов
> не отображаются в форме редактирования и на user-страницах).
>
> **Симптом закрыт** (commit `89e6bfe`): в `infra/nginx/nginx.prideclub.conf`
> добавлен блок `location ^~ /static/ { proxy_pass http://habit_backend; }` для
> `app.prideclub.fun`, `prideclub.fun`, `admin.prideclub.fun`. Backend через
> FastAPI StaticFiles (`apps/backend/app/main.py:166`) уже корректно отдаёт
> файлы из volume `club_uploads` с правильным `Content-Type`.
> Применение: `scp infra/nginx/nginx.prideclub.conf privichki-prod:/etc/nginx/sites-enabled/habit-club && ssh privichki-prod 'nginx -t && nginx -s reload'`.
> **Первопричина не закрыта** — нужно вернуть `build:` для `frontend` после
> диагностики overlay-конфликта.

**Deploy процедура (frontend):**

```bash
# 1. Локально: пересобрать bundle
cd apps/frontend
npm run build
# 2. На сервер: rsync bundle (dist/), НЕ пересобираем образ
rsync -az --delete apps/frontend/dist/ privichki-prod:/app/apps/frontend/dist/
# 3. Перезапустить контейнер (volume mount подхватится автоматически)
ssh privichki-prod 'cd /app/infra && docker compose up -d frontend'
# 4. Verify
ssh privichki-prod 'curl -s http://127.0.0.1:5173/ | grep -oE "main-[A-Za-z0-9]+\.js"'
```

**Когда можно вернуть `build:`:** после диагностики overlay-конфликта
(возможно через BuildKit cache mount или `docker buildx` с другим runtime).
См. отдельную задачу в репо.

### 9.2 Alembic upgrade через compose не выполняет ALTER TYPE ADD VALUE

**Симптом:** при `docker compose run --rm backend alembic upgrade head` после успешного
старта контейнера (`INFO [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO [alembic.runtime.migration] Will assume transactional DDL.`) — миграция НЕ применяется.
`SELECT * FROM alembic_version` показывает старую версию. В логах нет ни ошибок,
ни сообщения «running upgrade».

**Причина (предположительно):** миграция 015 (`015_checkin_status_extra_values.py`)
содержит `op.execute("ALTER TYPE checkin_status ADD VALUE IF NOT EXISTS 'joined_late'")`.
В PostgreSQL `ALTER TYPE ... ADD VALUE` не может выполняться внутри транзакции,
которую Alembic открывает по умолчанию (`transaction_per_migration=True`).
Контейнер останавливается с exit 0 без выполнения ALTER, потому что DDL был
откатан внутри транзакции, но Alembic не зафиксировал это в `alembic_version`.

**Workaround (применён 2026-08-09):** ручное применение миграции через psql
внутри контейнера postgres + обновление alembic_version:

```bash
# 1. На сервере: добавить enum-значения в БД напрямую
ssh privichki-prod 'docker exec habit-postgres psql -U habits -d habits -c "
  ALTER TYPE checkin_status ADD VALUE IF NOT EXISTS '\''joined_late'\'';
  ALTER TYPE checkin_status ADD VALUE IF NOT EXISTS '\''caught'\'';
"'
# 2. Обновить alembic_version чтобы синхронизировать state
ssh privichki-prod 'docker exec habit-postgres psql -U habits -d habits -c "
  UPDATE alembic_version SET version_num = '\''015_checkin_status_extra_values'\'';
"'
# 3. Verify
ssh privichki-prod 'docker exec habit-postgres psql -U habits -d habits -c "
  SELECT enum_range(NULL::checkin_status);
  SELECT version_num FROM alembic_version;
"'
# Должно быть: {done,missed,joined_late,caught} + 015_checkin_status_extra_values
```

**Альтернатива для будущих миграций:** в файле миграции использовать autocommit.
В Alembic 1.13+ нет встроенного декоратора `autocommit`, но можно обойти через
`op.execute("COMMIT")` перед `ALTER TYPE ADD VALUE` — но это внутри DDL-транзакции
alembic может не работать. Альтернатива: разделить ALTER TYPE на отдельную
миграцию с `op.execute("COMMIT"); op.execute("BEGIN")` обёрткой. Диагностика
первопричины — отдельная задача.

**Когда можно использовать `alembic upgrade head` через compose:** после фикса
autocommit-паттерна в alembic-op обёртке. До этого — ручной workaround выше.
