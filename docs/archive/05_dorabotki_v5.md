# Финальные доработки v5 — закрытие 3-4 часов правок перед первым коммитом

Последний документ цикла. Закрывает 6 пунктов, отмеченных как "3-4 часа правок"
перед тем, как пакет спецификаций можно считать действительно готовым к первому
коммиту бизнес-логики.

---

## 1. Audience claim в service token (30 мин)

```python
def generate_service_token(service_name: str, target_audience: str, secret: str, ttl_seconds: int = 60) -> str:
    now = int(time.time())
    payload = {
        "service": service_name,
        "aud": target_audience,   # кому предназначен токен, напр. "backend-api"
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def validate_service_token(token: str, secret: str, expected_audience: str) -> dict:
    return jwt.decode(
        token, secret, algorithms=["HS256"],
        audience=expected_audience,
        options={"leeway": 30},
        require=["exp", "iat", "service", "aud"],
    )
```

Bot Gateway и Worker при вызове Backend API указывают `target_audience="backend-api"`.
Если в будущем появится второй внутренний сервис (например, admin-api), у него будет
собственный audience — украденный токен для одного сервиса не сработает на другом.

## 2. Обработка ошибок валидации в auth_middleware (30 мин)

```python
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
        logger.warning("auth_failed", extra={"path": request.url.path, "ip": request.client.host, "reason": "invalid_init_data"})
        return JSONResponse({"code": "invalid_init_data"}, status_code=401)
    except InitDataExpiredError:
        logger.warning("auth_failed", extra={"path": request.url.path, "ip": request.client.host, "reason": "init_data_expired"})
        return JSONResponse({"code": "init_data_expired"}, status_code=401)
    except jwt.ExpiredSignatureError:
        return JSONResponse({"code": "service_token_expired"}, status_code=401)
    except jwt.InvalidTokenError:
        logger.warning("auth_failed", extra={"path": request.url.path, "ip": request.client.host, "reason": "invalid_service_token"})
        return JSONResponse({"code": "invalid_service_token"}, status_code=401)

    return await call_next(request)
```

Каждая ветка отказа возвращает структурированный JSON с понятным `code`, а не 500
со stacktrace — клиент (Mini App или бот) может обработать конкретную причину, а не
угадывать по тексту ошибки. Лог включает IP и путь, но никогда — содержимое initData
или токена.

## 3. Безопасный порядок миграции users.bonus_points (15 мин)

```sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users WHERE bonus_points > 0) THEN
        RAISE EXCEPTION 'users.bonus_points already populated — manual review required before running this migration';
    END IF;
END $$;

UPDATE users u SET bonus_points = COALESCE((
    SELECT SUM(m.bonus_points) FROM memberships m WHERE m.user_id = u.id
), 0)
WHERE EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id AND m.bonus_points > 0);

UPDATE memberships SET bonus_points = 0 WHERE bonus_points > 0;
```

Явная защитная проверка перед миграцией данных — если у `users.bonus_points` уже
есть непустые значения (признак того, что миграция запускается повторно или на
БД, где данные уже перенесены вручную), скрипт останавливается с ошибкой вместо
молчаливой потери данных.

## 4. Ротация бэкапов (1 час)

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
s3cmd put "${DUMP_FILE}.age" "s3://habits-backups/daily/"

# ротация: 7 ежедневных + 4 еженедельных + 12 месячных ≈ 23 файла
python3 rotate_backups.py --bucket habits-backups \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 12

rm -f "$DUMP_FILE" "${DUMP_FILE}.age"
```

`set -euo pipefail` гарантирует, что скрипт останавливается при любой ошибке в цепочке
(включая пустой пайп), а явная проверка `[ -s "$DUMP_FILE" ]` ловит случай, когда
`pg_dump` тихо создал пустой файл. Политика ротации ограничивает объём хранения
максимумом ~23 архивов вместо бесконечного роста.

## 5. Таймаут в /ready (15 мин)

```python
@app.get("/ready")
async def ready(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)):
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        await asyncio.wait_for(redis.ping(), timeout=2.0)
        return {"status": "ready"}
    except (asyncio.TimeoutError, Exception):
        raise HTTPException(503, "not ready")
```

Без таймаута зависший Redis блокировал бы readiness-проверку бесконечно; с таймаутом
оркестратор получает быстрый и предсказуемый ответ "не готов" вместо зависания.

## 6. Явный SQL для идемпотентной вставки штрафа без улова (15 мин)

```sql
INSERT INTO penalties (id, membership_id, catcher_membership_id, reason, amount, fund_share, date, created_at)
VALUES (gen_random_uuid(), :membership_id, NULL, 'window_closed_no_catch', :amount, :amount, CURRENT_DATE, now())
ON CONFLICT (membership_id, date, reason) DO NOTHING
RETURNING id;
```

Если запрос не возвращает `id` (конфликт сработал) — приложение считает штраф уже
обработанным и не запускает повторное списание депозита или отправку уведомления.

---

## Обновлённый чек-лист "до первого коммита бизнес-логики"

- [ ] VPS в РФ настроен, Docker Compose + Let's Encrypt работают
- [ ] Все SQL-миграции применены на пустой БД, включая защитную проверку из п. 3
- [ ] `validate_init_data` + `validate_service_token` (leeway + audience) + `auth_middleware` с try/except реализованы
- [ ] CORS настроен на `web.telegram.org`, OPTIONS пропускается без auth
- [ ] `/health` и `/ready` (с таймаутами) отвечают корректно
- [ ] `backup_cron.sh` с `set -euo pipefail`, проверкой размера файла и ротацией пишет в Selectel Object Storage
- [ ] Секреты вынесены из репозитория, `.env` с правами 600
- [ ] Правило "не логировать PII" внедрено в middleware
- [ ] Идемпотентная вставка `window_closed_no_catch` реализована по явному SQL из п. 6

## Перенесено в "желательно до релиза MVP" (не блокирует старт)

Алерты в Telegram (/ready падает > 1 мин, диск > 80%, 0 чек-инов за час), тест
миграций в CI (`upgrade head → downgrade base → upgrade head`), smoke-test после
деплоя, короткий `RUNBOOK.md` для оператора с инструкциями восстановления из
бэкапа и отката миграции.

---

## Статус пакета

Цикл спецификаций (концепция → архитектура → структура → стандарты → доработки
v1–v5) закрыт. Все критические классы рисков — юридическая модель, антифрод,
race conditions, безопасность аутентификации, соответствие ФЗ-152, disaster
recovery — имеют явное техническое решение с конкретным кодом или SQL, а не
общими формулировками. Пакет готов к передаче команде разработки как техническое
задание для старта первой итерации MVP.
