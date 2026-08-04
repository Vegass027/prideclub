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
