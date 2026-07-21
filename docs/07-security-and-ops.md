# 07 — Безопасность и операционные процессы

Хостинг, аутентификация, ФЗ-152, бэкапы, секреты, мониторинг. Все решения закрыты
для соответствия требованиям production-системы с денежной механикой.

---

## 1. Хостинг

### Решение: VPS в РФ (Selectel) на старте

| Критерий | VPS в РФ | Yandex Cloud | Зарубежный |
|---|---|---|---|
| Соответствие ФЗ-152 | ✅ | ✅ | ❌ блокер |
| Стоимость на MVP | Низкая | Средняя–высокая | Низкая (но нелегитимно) |
| Ops-нагрузка | Высокая | Низкая | Низкая |
| Путь роста | Миграция на managed | Готов сразу | Недопустим |

**Обоснование:** минимизирует расходы на старте без подтверждённой выручки, полностью
закрывает 152-ФЗ. При росте — миграция на **Yandex Cloud managed PostgreSQL/Redis**
без изменения кода приложения (тот же Docker-образ).

### Правило
**Никакая часть проекта, хранящая ПДн российских пользователей, не размещается за
пределами РФ** — при масштабировании мигрирует инфраструктура, не география хранения.

### Инфраструктура

```
infra/
├── docker-compose.prod.yml
├── nginx/nginx.conf             # HTTPS через Let's Encrypt
├── backup/
│   ├── backup_cron.sh
│   ├── rotate_backups.py
│   └── restore_test.sh
└── docker/
    ├── backend.Dockerfile
    ├── bot.Dockerfile
    ├── worker.Dockerfile
    └── frontend.Dockerfile
```

- PostgreSQL и Redis — контейнеры на том же VPS (для MVP до нескольких тысяч пользователей).
- `ufw`/firewall: открыты только 443 (HTTPS) и SSH по ключу.
- HTTPS через Let's Encrypt (Certbot) с автопродлением.

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

### Стратегия

- Ежедневный `pg_dump` → шифрование (age/gpg) → загрузка в **Selectel Object Storage**
  (отдельный от VPS ресурс, тот же регион РФ — соответствие ФЗ-152 сохраняется).
- Еженедельный тестовый restore в изолированный контейнер — подтверждает, что бэкап
  реально восстанавливается.
- **Целевые показатели:** RPO ≤ 24 часа, RTO ≤ 4 часа.
- Heartbeat-файл `heartbeat/last_success.txt` перезаписывается при каждом успешном
  бэкапе — внешний мониторинг проверяет свежесть раз в час, алертит если старше 26 часов.
- Ротация: 7 ежедневных + 4 еженедельных + 12 месячных ≈ 23 архива.

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

### Метрики (Prometheus)

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
| `bonus_applied=true` без связанной `transactions` | Владелец | Аудит-инцидент |

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
