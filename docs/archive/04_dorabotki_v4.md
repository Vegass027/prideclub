# Финальные доработки v4 — пакет "до первого коммита"

Закрывает пункты, помеченные ревьюером как обязательные до первого коммита бизнес-логики.
После этого документа пакет специфийкаций (koncepciya → arhitektura → struktura →
standarty → dorabotki v1-v4) готов к передаче разработчику как ТЗ.

---

## 1. Clock skew в validate_service_token

```python
def validate_service_token(token: str, secret: str) -> dict:
    payload = jwt.decode(
        token, secret, algorithms=["HS256"],
        options={"leeway": 30},   # допускаем 30 сек расхождения часов
        require=["exp", "iat", "service"],
    )
    return payload

def generate_service_token(service_name: str, secret: str, ttl_seconds: int = 60) -> str:
    now = int(time.time())
    payload = {"service": service_name, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, secret, algorithm="HS256")
```

Переход с ручной проверки `iat` на стандартный `exp` claim + `leeway=30` — устойчиво
к расхождению часов между VPS-контейнерами и не требует ручной арифметики времени.

## 2. CORS и OPTIONS в auth_middleware

```python
ALLOWED_ORIGINS = ["https://web.telegram.org"]

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)  # preflight пропускается без auth-проверки
    ...

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Telegram-Init-Data", "X-Service-Token", "Content-Type"],
)
```

## 3. Health и readiness эндпоинты

```python
@app.get("/health")
async def health():
    return {"status": "ok"}  # liveness — жив ли процесс

@app.get("/ready")
async def ready(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)):
    try:
        await db.execute(text("SELECT 1"))
        await redis.ping()
        return {"status": "ready"}
    except Exception:
        raise HTTPException(503, "not ready")
```

## 4. SQL-миграции (создаются как единый набор до старта кодирования)

```sql
-- 001_bonus_and_penalty_fixes.sql
ALTER TABLE penalties ADD COLUMN bonus_applied BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE penalties ADD COLUMN reason VARCHAR NOT NULL DEFAULT 'caught';
ALTER TABLE penalties ADD COLUMN date DATE NOT NULL DEFAULT CURRENT_DATE;

CREATE UNIQUE INDEX uq_penalty_per_day_reason
ON penalties (membership_id, date, reason);

ALTER TABLE users ADD COLUMN bonus_points BIGINT NOT NULL DEFAULT 0;  -- BIGINT, не INT
ALTER TABLE users ADD COLUMN bonus_points_updated_at TIMESTAMPTZ;
ALTER TABLE memberships ADD COLUMN bonus_points BIGINT NOT NULL DEFAULT 0;

-- снапшот призовых правил как таблица, не JSONB
CREATE TABLE season_prize_rules_snapshot (
    season_id UUID NOT NULL REFERENCES seasons(id),
    metric VARCHAR NOT NULL,
    rank_from INT NOT NULL,
    rank_to INT NOT NULL,
    percentage NUMERIC(5,2) NOT NULL,
    PRIMARY KEY (season_id, metric, rank_from, rank_to)
);

-- миграция существующих bonus_points на users.id (если применимо)
UPDATE users u SET bonus_points = COALESCE((
    SELECT SUM(m.bonus_points) FROM memberships m WHERE m.user_id = u.id
), 0);
UPDATE memberships SET bonus_points = 0;
```

Валидация призовых правил дополнена проверкой диапазона рангов:

```python
def validate_prize_rules(rules: list[PrizeRule]):
    for rule in rules:
        if rule.rank_from < 1 or rule.rank_from > rule.rank_to:
            raise InvalidPrizeRulesError(f"invalid range {rule.rank_from}-{rule.rank_to}")
    by_metric = defaultdict(float)
    for rule in rules:
        by_metric[rule.metric] += float(rule.percentage)
    for metric, total in by_metric.items():
        if abs(total - 100.0) > 0.01:
            raise InvalidPrizeRulesError(f"{metric} sums to {total}, expected 100")
```

Защита `close_catch_window_job` от двойного запуска — `INSERT ... ON CONFLICT DO NOTHING`
поверх уникального индекса `(membership_id, date, reason)`, безопасно при повторном
срабатывании cron.

## 5. Backup-стратегия (VPS в РФ, за пределами того же сервера)

- Ежедневный `pg_dump` → шифрование (age/gpg) → загрузка в **Selectel Object Storage**
  (отдельный от VPS ресурс, тот же регион РФ — соответствие ФЗ-152 сохраняется).
- Еженедельный тестовый restore в изолированный staging-контейнер — подтверждает,
  что бэкап реально восстанавливается, а не просто создаётся.
- Целевые показатели: **RPO ≤ 24 часа, RTO ≤ 4 часа**.
- Мониторинг диска: алерт в Telegram владельцу при заполнении > 80%.

## 6. Управление секретами

Для MVP: файл `.env` на сервере с `chmod 600`, не в репозитории (в `.gitignore`),
загружается через защищённый CI/CD secret store (GitHub Actions secrets) при деплое.
Секреты: `BOT_TOKEN`, `SERVICE_SECRET`, `POSTGRES_PASSWORD`, `WEBHOOK_SECRET`.
Правило: секреты никогда не попадают в логи и не передаются как query-параметры.

## 7. Логирование без PII

**Правило проекта: `request.state.telegram_user` (first_name, username) никогда не
логируется целиком — только числовой `user_id`.** Неудачные попытки аутентификации
логируются структурированно (IP, путь, `claimed_service`), без секретов и без PII.

## 8. Проверка целостности bonus ↔ transactions (ежедневный cron)

```sql
SELECT p.id FROM penalties p
WHERE p.bonus_applied = true
  AND NOT EXISTS (
    SELECT 1 FROM transactions t
    WHERE t.related_penalty_id = p.id AND t.type = 'bonus_catch'
  );
```

При непустом результате — алерт владельцу в Telegram, ручная проверка.

## 9. Уведомление о сгорании бонусов

Cron за 7 дней до `expire_stale_bonus_points` отправляет сообщение через бота (если
`notifications_enabled = true`): "У вас сгорает N бонусных баллов через 7 дней."

---

## Чек-лист "до первого коммита бизнес-логики"

- [ ] VPS в РФ настроен, Docker Compose + Let's Encrypt работают
- [ ] Все SQL-миграции из раздела 4 применены на пустой БД
- [ ] `validate_init_data` + `validate_service_token` (с leeway) + `auth_middleware` реализованы
- [ ] CORS настроен на `web.telegram.org`, OPTIONS пропускается без auth
- [ ] `/health` и `/ready` отвечают корректно
- [ ] `backup_cron.sh` пишет в Selectel Object Storage, тестовый restore пройден
- [ ] Секреты вынесены из репозитория, `.env` с правами 600
- [ ] Правило "не логировать PII" внедрено в middleware

## Что переносится в backlog (после MVP, не блокирует старт)

Модерация чатов, DR-тестирование по полному сценарию, ADR-документация, rate limiting
на уровне middleware, промышленный мониторинг бизнес-аномалий (0 чек-инов в клубе,
всплеск штрафов, всплеск регистраций), капча при регистрации.

---

## Итог по всему циклу ревью

Пакет документов (концепция → архитектура → структура проекта → стандарты кода →
доработки v1–v4) закрывает: продуктовую механику, юридическую модель денег, схему
БД с антифродом и идемпотентностью, UI/UX, service-to-service безопасность,
хостинг с учётом ФЗ-152 и операционные процессы (бэкапы, health-checks, секреты).
Документы готовы как основа технического задания для команды разработки.
