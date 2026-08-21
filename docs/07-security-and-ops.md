# 07 — Безопасность и операционные процессы

> Snapshot от 2026-07-22 (обновлено 2026-08-07 после Step 7 — успешный деплой
> SSE+Redis Streams). **Хостинг = Contabo VPS 4 (Германия), не Selectel (РФ)** —
> см. §1. Бэкапы и Sentry/Grafana **не развёрнуты** на проде, см. §4 и §7.
> Auth-контур в §2 актуален и работает. **SSE_TOKEN_SECRET** добавлен в §2.4
> (отдельный секрет для SSE-контура, не шарящийся с `SERVICE_SECRET`).

Хостинг, аутентификация, ФЗ-152, бэкапы, секреты, мониторинг. Все решения закрыты
для соответствия требованиям production-системы с денежной механикой.

---

## 1. Хостинг

### Текущая конфигурация: Contabo Cloud VPS 4 (Германия, **не РФ**)

> ⚠️ **Расхождение с конценцией.** Документ изначально описывал план хостинга
> (Selectel VPS в РФ), но на 2026-07-22 фактический сервер — **Contabo Cloud VPS 4**
> (4 vCPU / 8 GB / 100 GB SSD), `169.58.52.78`, Ubuntu 24.04. Это **нарушает
> предполагавшееся правило** "никакая часть проекта с ПДн российских пользователей
> не размещается за пределами РФ". Миграция на Selectel managed / Yandex Cloud
> — в плане, но не выполнена. На проде сейчас 10 users / 0 habits / 0 transactions
> (только тест-регистрации), реальных ПДн клиентов нет.

| Параметр | Значение |
|---|---|
| Провайдер | Contabo Cloud VPS 4 |
| IP | `169.58.52.78` |
| OS | Ubuntu 24.04 LTS |
| CPU | 4 vCPU |
| RAM | 8 GB |
| Диск | 100 GB SSD |
| Регион | Германия (ЕС) |

### Целевая конфигурация (в плане, не выполнена): VPS в РФ (Selectel)

| Критерий | VPS в РФ | Yandex Cloud | Зарубежный (текущее) |
|---|---|---|---|
| Соответствие ФЗ-152 | ✅ | ✅ | ❌ под риском |
| Стоимость на MVP | Низкая | Средняя–высокая | Низкая |
| Ops-нагрузка | Высокая | Низкая | Низкая |
| Путь роста | Миграция на managed | Готов сразу | Недопустимо для прод-нагрузки |

**План:** при росте — миграция на **Yandex Cloud managed PostgreSQL/Redis** без
изменения кода приложения (тот же Docker-образ).

### Правило
**Никакая часть проекта, хранящая ПДн российских пользователей, не размещается за
пределами РФ** — при масштабировании мигрирует инфраструктура, не география хранения.

### Инфраструктура на текущем сервере

```
infra/
├── docker-compose.yml          # 7 сервисов (postgres, redis, backend, bot, worker, frontend, pgweb)
├── nginx/                      # референсные конфиги (на проде nginx на хосте, не в контейнере)
│   ├── frontend.nginx.conf
│   └── nginx.conf, nginx.prideclub.conf, nginx.prod.conf, prideclub.tls.conf
├── backup/                     # backup_cron.sh готов, НЕ развёрнут (см. §4)
│   ├── backup_cron.sh
│   ├── rotate_backups.py
│   └── restore_test.sh
├── docker/
│   ├── backend.Dockerfile      # python:3.12-slim
│   ├── bot.Dockerfile          # python:3.12-slim
│   ├── worker.Dockerfile       # python:3.12-slim
│   └── frontend.Dockerfile     # multi-stage: node:20-alpine → nginx:1.27-alpine
├── deploy.sh                   # rsync + build + up -d + register webhook
└── setup_server.sh             # первоначальная настройка Ubuntu 24.04
```

- PostgreSQL 16 и Redis 7 — контейнеры на том же VPS.
- `ufw`/firewall: открыты 80, 443 (Let's Encrypt) и 22 (SSH).
- HTTPS через Let's Encrypt (Certbot) с автопродлением, по сертификату на домен.
- На хосте **двухслойный nginx**: `/etc/nginx/sites-enabled/habit-club` проксирует
  на `127.0.0.1:{5173,8000,8080,8081}`, а внутри `habit-frontend` крутится
  собственный `nginx:1.27-alpine` для отдачи статики.

---

## 2. Аутентификация

### Два контура

```
/api/v1/*  → пользовательские запросы от Mini App → проверка X-Telegram-Init-Data
/internal/* → запросы от Bot/Worker → проверка X-Service-Token (JWT)
```

**Разные URL — разные уровни доверия.** Случайная утечка внутреннего токена в
пользовательский эндпоинт (или наоборот) невозможна.

### 2.1. Валидация initData (пользователь)

```python
import hashlib, hmac
from urllib.parse import parse_qsl
import time

def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict:
    parsed = dict(parse_qsl(init_data))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise InvalidInitDataError()

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise InvalidInitDataError()

    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > max_age_seconds:
        raise InitDataExpiredError()

    return parsed  # содержит user (json), auth_date
```

Три уровня защиты:
- HMAC-SHA256 с секретом `WebAppData` — подделка невозможна без токена бота.
- `hmac.compare_digest` — защита от timing-атак.
- Проверка `auth_date` с TTL — защита от replay-атак со старыми данными.

### 2.2. Service Token (bot/worker)

```python
import jwt, time

def generate_service_token(
    service_name: str, target_audience: str, secret: str, ttl_seconds: int = 60
) -> str:
    now = int(time.time())
    payload = {
        "service": service_name,
        "iss": service_name,
        "aud": target_audience,  # напр. "backend-api"
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def validate_service_token(token: str, secret: str, expected_audience: str) -> dict:
    return jwt.decode(
        token, secret, algorithms=["HS256"],
        audience=expected_audience,
        options={"leeway": 30},
        require=["exp", "iat", "service", "aud", "iss"],
    )
```

- Short-lived (60 сек) — защита от replay.
- `aud` (audience) — токен для одного сервиса не работает в другом.
- `iss` (issuer) — аудит источника.
- `leeway=30` — устойчивость к расхождению часов.

### 2.4. SSE Token (Mini App real-time updates)

> Реализовано 2026-08-04 (Step 1, commit `c836542`). **Активно используется** —
> при чек-ине через бота Mini App получает real-time обновление статуса через
> SSE+Redis Streams без polling.

**`POST /api/v1/events/stream/token`** выдаёт JWT-токен для подключения к SSE.
Подписан **отдельным** секретом `SSE_TOKEN_SECRET` (HS256, НЕ `SERVICE_SECRET` —
разные контуры, разные секреты, разная blast-radius при компрометации):

```
JWT claims:
  sub:           user_id (numeric)
  habit_id:      UUID
  scope:         "sse:today"
  aud:           "sse-stream"
  iss:           "backend"
  iat, exp:      now, now+60s
TTL:             60 секунд (НЕ одноразовый — осознанное решение Q4)
Leeway:          10с на валидации (дрейф часов + reconnect-флоу)
```

```python
def generate_sse_token(user_id: int, habit_id: UUID) -> str:
    return jwt.encode({
        "sub": user_id, "habit_id": str(habit_id),
        "scope": "sse:today", "aud": "sse-stream",
        "iss": "backend", "iat": now, "exp": now + 60,
    }, settings.SSE_TOKEN_SECRET, algorithm="HS256")
```

**Почему `SSE_TOKEN_SECRET` ≠ `SERVICE_SECRET`:**

| Сценарий | `SERVICE_SECRET` | `SSE_TOKEN_SECRET` |
|---|---|---|
| URL | `/internal/*` (bot ↔ backend ↔ worker) | `/api/v1/events/*` (Mini App ↔ backend) |
| Уровень доверия | trusted services (контейнеры) | untrusted user input (Telegram WebView) |
| Что даёт компрометация | бот может вызывать `/internal/payments/confirm` от любого юзера | атакующий может читать SSE-стримы юзеров (read-only) |
| TTL | 60 с (тот же, паттерн тот же) | 60 с |
| Rotation impact | затронет bot + worker | затронет только Mini App |

Если придётся ротировать `SSE_TOKEN_SECRET` (например, лог-утечка токена
в access-логе nginx до применения `access_log off` для `/api/v1/events/stream`,
см. `docs/archive/2026-summer-fixes/sse+redis.md §3.2`) — это не затронет internal-контур.

**Membership-check на этапе выдачи токена (НЕ на стриме):**
- Fail-fast — юзер сразу видит 403 `membership_not_active`, не открывает EventSource зря.
- Паразитный трафик исключён (пустой стрим с XREAD 30 с блокирует воркер).
- Через 60 с токен протухнет, но membership не мог измениться так быстро → повторный
  check на стриме избыточен (+1 RTT к БД).

**`GET /api/v1/events/stream`** (НЕ требует initData — exact-path bypass в
`core/middleware.py`):
- `SSE_AUTH_BYPASS_PATHS = {"/api/v1/events/stream"}` — точный set, не префикс
  (фикс-up 1 `a0217ec` + тест `test_similar_path_under_events_is_not_bypassed`).
  `POST /events/stream/token` остаётся под initData-middleware.

**Per-user concurrency limit** (см. `docs/archive/2026-summer-fixes/sse+redis.md §2.6`):
`MAX_CONCURRENT_CONNECTIONS_PER_USER = 5` через Lua-atomic `INCR + EXPIRE + DECR-rollback`
в `services/sse/connection_limiter.py`. Защита от DoS через replayable token (TTL=60с,
один валидный токен не открывает неограниченное число соединений в окне 60с).

### 2.3. Middleware

```python
ALLOWED_ORIGINS = ["https://web.telegram.org"]

def get_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    try:
        if request.url.path.startswith("/internal/"):
            token = request.headers.get("X-Service-Token")
            if not token:
                return JSONResponse({"code": "missing_service_token"}, status_code=401)
            payload = validate_service_token(token, settings.SERVICE_SECRET, "backend-api")
            request.state.caller = ServiceCaller(name=payload["service"])

        elif request.url.path.startswith("/api/v1/"):
            init_data = request.headers.get("X-Telegram-Init-Data")
            if not init_data:
                return JSONResponse({"code": "missing_init_data"}, status_code=401)
            validated = validate_init_data(init_data, settings.BOT_TOKEN)
            request.state.telegram_user = json.loads(validated["user"])

        else:
            return JSONResponse({"code": "not_found"}, status_code=404)

    except InvalidInitDataError:
        logger.warning("auth_failed", extra={
            "path": request.url.path, "ip": get_client_ip(request),
            "reason": "invalid_init_data",
        })
        return JSONResponse({"code": "invalid_init_data"}, status_code=401)
    except InitDataExpiredError:
        logger.warning("auth_failed", extra={
            "path": request.url.path, "ip": get_client_ip(request),
            "reason": "init_data_expired",
        })
        return JSONResponse({"code": "init_data_expired"}, status_code=401)
    except jwt.ExpiredSignatureError:
        return JSONResponse({"code": "service_token_expired"}, status_code=401)
    except jwt.InvalidTokenError:
        logger.warning("auth_failed", extra={
            "path": request.url.path, "ip": get_client_ip(request),
            "reason": "invalid_service_token",
        })
        return JSONResponse({"code": "invalid_service_token"}, status_code=401)

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Telegram-Init-Data", "X-Service-Token", "Content-Type"],
    max_age=3600,
)
```

**Правило:** ни один эндпоинт не принимает `user_id` как параметр запроса — только
из `request.state.telegram_user`, установленного middleware после проверки подписи.

---

## 3. Health и readiness

```python
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)):
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        await asyncio.wait_for(redis.ping(), timeout=2.0)
        return {"status": "ready"}
    except (asyncio.TimeoutError, Exception):
        raise HTTPException(503, "not ready")
```

- `/health` (liveness) — жив ли процесс.
- `/ready` (readiness) — БД и Redis отвечают, трафик можно слать.
- Таймауты 2 сек на каждую проверку — fail-fast.

---

## 4. Бэкапы

### Стратегия (план)

- Ежедневный `pg_dump` → шифрование (age/gpg) → загрузка в **Selectel Object Storage**
  (отдельный от VPS ресурс, тот же регион РФ — соответствие ФЗ-152 сохраняется).
- Еженедельный тестовый restore в изолированный контейнер — подтверждает, что бэкап
  реально восстанавливается.
- **Целевые показатели:** RPO ≤ 24 часа, RTO ≤ 4 часа.
- Heartbeat-файл `heartbeat/last_success.txt` перезаписывается при каждом успешном
  бэкапе — внешний мониторинг проверяет свежесть раз в час, алертит если старше 26 часов.
- Ротация: 7 ежедневных + 4 еженедельных + 12 месячных ≈ 23 архива.

### Текущий статус (2026-07-22): **бэкапы НЕ развёрнуты**

- `infra/backup/backup_cron.sh` готов (pg_dump | gzip | age | s3).
- На сервере **нет** `aws` CLI (не установлен).
- В `/app/.env` **нет** `S3_*` env-переменных (нет endpoint, bucket, ключей).
- Cron-задача `/etc/cron.d/habit-backup` **не зарегистрирована**.
- **Текущая защита данных:** только Docker volume `habit-club_pgdata` на хосте. При
  потере VPS — потеря всех данных (БД + Redis + uploads).

### backup_cron.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

DUMP_FILE="/tmp/backup_$(date +%Y%m%d_%H%M%S).sql.gz"
pg_dump "$DATABASE_URL" | gzip > "$DUMP_FILE"

if [ ! -s "$DUMP_FILE" ]; then
    echo "ERROR: backup file is empty" | notify_telegram_admin
    exit 1
fi

age -e -r "$BACKUP_PUBLIC_KEY" -o "${DUMP_FILE}.age" "$DUMP_FILE"

aws --endpoint-url="$S3_ENDPOINT_URL" \
    s3 cp "${DUMP_FILE}.age" "s3://$S3_BACKUP_BUCKET/daily/$(basename ${DUMP_FILE}).age"

aws --endpoint-url="$S3_ENDPOINT_URL" \
    s3api put-object --bucket "$S3_BACKUP_BUCKET" --key "heartbeat/last_success.txt" \
    --body <(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 rotate_backups.py --bucket "$S3_BACKUP_BUCKET" \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 12

rm -f "$DUMP_FILE" "${DUMP_FILE}.age"
```

- `set -euo pipefail` — остановка при любой ошибке.
- Проверка размера файла — ловит "пустой бэкап".
- `aws-cli` с кастомным endpoint на Selectel S3 (не устаревший `s3cmd`).
- Heartbeat записывается только при успехе.

---

## 5. Управление секретами

### Правила

- `BOT_TOKEN`, `SERVICE_SECRET`, `POSTGRES_PASSWORD`, `WEBHOOK_SECRET` — в `.env` на
  сервере с `chmod 600`, **не в репозитории** (`.gitignore`).
- Загрузка в прод через защищённый CI/CD secret store (GitHub Actions secrets).
- Секреты **никогда не попадают в логи** и не передаются как query-параметры.

### ФЗ-152 — логирование

**Правило:** `request.state.telegram_user` (first_name, username) **никогда не
логируется целиком** — только числовой `user_id`. Неудачные попытки аутентификации
логируются структурированно (IP, путь, `reason`), без секретов и без PII.

---

## 6. ФЗ-152 — соответствие

### Перечень обрабатываемых ПДн
- Telegram `user_id`, `username`, `first_name`.
- Фиксируется в политике обработки ПДн (публикуется в Mini App / на сайте).

### Согласие на обработку
- При первом платеже через нативный `showConfirm` с текстом оферты и ссылкой.
- Фиксируется в `user_consents` с версией оферты, временем и IP.

### Право на удаление
- Команда `/delete_my_data` в боте или кнопка в Mini App (Профиль).
- Персональные данные анонимизируются (`data_anonymized=true`, `username=NULL`).
- **Финансовая история** (`transactions`, `penalties`) сохраняется в анонимизированном
  виде — требования бухгалтерского/налогового учёта имеют приоритет.

### Срок хранения
- Данные активного аккаунта + 3 года после последнего платежа (стандарт для финансовых
  документов), затем архивируются.

### Хранение
- Только на территории РФ — Selectel VPS, Selectel Object Storage.

---

## 7. Мониторинг и алерты

### Текущий статус (2026-07-22)

- **Sentry:** SDK подключён в backend (`sentry-sdk[fastapi]==2.19.2` в
  `apps/backend/requirements.txt`). На проде `SENTRY_DSN` пуст → **no-op**.
  Ошибки в Sentry **не отправляются**.
- **Prometheus:** endpoint `/metrics` отдаёт дефолтные метрики Python +
  процесса (`prometheus_client`). **Кастомные метрики** (`habit_*`) **не
  заведены**.
- **Grafana:** **не развёрнута** на сервере.
- **Алерты в Telegram:** **не настроены**.
- **Структурированные логи:** structlog + JSONRenderer в `apps/backend/app/core/logging.py`
  и `apps/worker/worker/logging_setup.py` — backend и worker пишут JSON в stdout.
  **Bot логирует plain text** (`bot/main.py:25` использует `logging.basicConfig` вместо
  structlog-JSON) — не соответствует backend/worker.

### Целевая схема (план)

| Метрика | Тип | Назначение |
|---|---|---|
| `auth_failures_total{reason}` | Counter | Детекция атак |
| `auth_success_total{service}` | Counter | Нагрузка |
| `http_request_duration_seconds{path}` | Histogram | SLA |
| `order_execution_duration_seconds` | Histogram | Бизнес-операции |
| `celery_task_duration_seconds` | Histogram | Фоновые задачи |

### Продуктовые метрики

| Метрика | Как считать | Цель |
|---|---|---|
| Daily Active Check-ins | `count(checkins) / count(active memberships)` | Здоровье продукта |
| Конверсия "вступление → 1-й чек-ин" | `% memberships с checkins за 24ч после joined_at` | Качество онбординга |
| Конверсия "1-й чек-ин → 7 дней" | Из `daily_streak_snapshots` | Удержание |
| Средний штраф / частота | `avg(amount)`, `count(penalties) / memberships` | Дисциплина |
| SLA обработки чек-ина | Время от `message.date` до записи в `checkins` | p95 < 30 сек |

### Алерты в Telegram

| Триггер | Канал | Действие |
|---|---|---|
| `/ready = 503` дольше 1 минуты | Владелец | Проверить БД/Redis |
| Диск VPS > 80% | Владелец | Очистка логов, увеличение диска |
| `heartbeat/last_success.txt` старше 26 часов | Владелец | Backup не работает |
| 0 чек-инов в клубе N за последний час | Владелец | Возможна поломка бота |
| Всплеск штрафов > X за день | Владелец | Возможна атака или сбой логики |
| Всплеск регистраций > Y за час | Владелец | Вирусный эффект или бот-атака |

*REMOVED Phase 8 (2026-08-21):* `bonus_applied=true` без связанной `transactions`
— бонусная механика удалена, alert убран. |

---

## 8. CI/CD

### Backend pipeline
1. Lint: `ruff check .` + `mypy app/`.
2. Test: `pytest -v` (юнит + интеграционные).
3. Migration test: `alembic upgrade head → downgrade base → upgrade head`.
4. Build Docker-образ.
5. Push в registry.

### Frontend pipeline
1. Lint: `eslint .` + `tsc --noEmit`.
2. Test: `vitest`.
3. Build: `vite build`.
4. Деплой статики.

### Dependabot
Еженедельная проверка Python-зависимостей и Docker-образов, PR создаются автоматически,
мерджатся после прохождения CI и ручного review.

---

## 9. Обновления безопасности

- Docker-образы на VPS регулярно обновляются (CVE в PostgreSQL, Redis, Python).
- Dependabot PR для зависимостей мерджатся **после прохождения полного тест-плана**,
  не через автоматический `pip install -U`.
- `requirements.txt` использует **точные версии** (`==`), не диапазоны.
