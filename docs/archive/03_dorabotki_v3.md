# Финальные доработки v3 — закрытие блокеров перед стартом кодирования

Документ закрывает 3 блокирующих пункта из третьего ревью и ключевые доработки
(race condition, идемпотентность, UI для edge-кейсов). После этого документа
проект готов как основа технического задания.

---

## БЛОКЕР 1 — Выбор хостинга (ФЗ-152 vs Railway/Render)

**Решение: Вариант A — VPS в РФ (Selectel) на старте, с чётким планом миграции.**

### Обоснование выбора

| Критерий | VPS в РФ (Selectel/Timeweb) | Yandex Cloud | Зарубежный (Railway/Render) |
|---|---|---|---|
| Соответствие ФЗ-152 | Да | Да | Нет — блокер |
| Стоимость на MVP (до 1000 пользователей) | Низкая | Средняя–высокая | Низкая, но нелегитимна |
| Ops-нагрузка | Высокая (сам настраиваешь PG, Redis) | Низкая (managed-сервисы) | Низкая |
| Скорость старта | Средняя (1–2 дня на настройку) | Быстрая | Самая быстрая |
| Путь роста | Миграция на managed по мере роста | Готов к росту сразу | Недопустим |

Для MVP выбран **VPS в РФ + Docker Compose** — минимизирует расходы на старте
(проект без подтверждённой выручки), при этом полностью закрывает требование
152-ФЗ о хранении ПДн россиян на территории РФ. При росте нагрузки (после
подтверждения продукта) — миграция на **Yandex Cloud managed PostgreSQL/Redis**
без изменения кода приложения (тот же Docker-образ, просто меняется конфигурация
подключения).

### Что меняется в инфраструктуре

```
infra/
├── docker-compose.prod.yml       # postgres, redis, backend, bot, worker, nginx — всё на одном VPS
├── nginx/
│   └── nginx.conf                # HTTPS через Let's Encrypt, reverse proxy
└── backup/
    └── backup_cron.sh            # ежедневный pg_dump → шифрованный архив
```

- PostgreSQL и Redis разворачиваются как контейнеры на том же VPS (для MVP это
  приемлемо при нагрузке до нескольких тысяч пользователей).
- Обязательна настройка `ufw`/firewall — открыты только порты 443 (HTTPS) и SSH
  по ключу, PostgreSQL/Redis недоступны снаружи.
- HTTPS обязателен и для Mini App, и для webhook бота — через Let's Encrypt
  (Certbot) с автопродлением.

Правило зафиксировано: **никакая часть проекта, хранящая ПДн российских
пользователей (users, memberships, transactions), не размещается за пределами РФ**,
независимо от дальнейшего роста — при масштабировании мигрирует инфраструктура,
не география хранения данных.

---

## БЛОКЕР 2 — Service-to-service аутентификация (бот → Backend API)

Вводится отдельный механизм для запросов, которые идут не от Mini App (с initData),
а от Bot Gateway или Worker к Backend API.

```python
# core/security.py
def generate_service_token(service_name: str, secret: str) -> str:
    payload = {"service": service_name, "iat": int(time.time())}
    return jwt.encode(payload, secret, algorithm="HS256")

def validate_service_token(token: str, secret: str, max_age_seconds: int = 60) -> dict:
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    if time.time() - payload["iat"] > max_age_seconds:
        raise ServiceTokenExpiredError()
    return payload
```

Токен создаётся заново на **каждый запрос** (short-lived, 60 секунд) — bot/worker
подписывают его общим секретом `SERVICE_SECRET` из переменных окружения, недоступным
извне.

### Два режима middleware

```python
PUBLIC_PATHS = ("/api/v1/",)
INTERNAL_PATHS = ("/internal/",)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.url.path.startswith(INTERNAL_PATHS):
        token = request.headers.get("X-Service-Token")
        payload = validate_service_token(token, settings.SERVICE_SECRET)
        request.state.caller = ServiceCaller(name=payload["service"])
    elif request.url.path.startswith(PUBLIC_PATHS):
        init_data = request.headers.get("X-Telegram-Init-Data")
        validated = validate_init_data(init_data, settings.BOT_TOKEN)
        request.state.telegram_user = json.loads(validated["user"])
    else:
        raise HTTPException(404)
    return await call_next(request)
```

Разделение путей: пользовательские запросы от Mini App идут на `/api/v1/...`
(проверка initData), внутренние запросы от бота/воркера — на `/internal/...`
(проверка service-token). Это чёткая граница, которая не даёт спутать два разных
уровня доверия.

**Правило проекта: Bot Gateway никогда не хранит и не использует чужой initData
для вызова API от имени пользователя — только собственный service-token,
и передаёт `user_id` явно как параметр, потому что уже сам аутентифицирован
через Telegram Bot API токен.**

---

## БЛОКЕР 3 — Race condition в BonusService (с идемпотентностью)

Исправлено добавлением блокировки строки и идемпотентности на основе конкретного
события (улова), а не просто счётчика.

```sql
ALTER TABLE penalties ADD COLUMN bonus_applied BOOLEAN NOT NULL DEFAULT false;
```

```python
class BonusService:
    async def apply_catch_bonus(self, catcher_membership_id: UUID, penalty_id: UUID):
        async with self._db.begin():
            penalty = await self._penalty_repo.lock_for_update(penalty_id)
            if penalty.bonus_applied:
                return  # идемпотентность: бонус за этот конкретный улов уже начислен

            membership = await self._membership_repo.lock_for_update(catcher_membership_id)
            membership.bonus_points += 1

            rule = await self._bonus_rules_repo.get("catch", threshold=5)
            if membership.bonus_points % rule.threshold == 0:
                await self._grant_reward(membership, rule)

            penalty.bonus_applied = True
            await self._db.flush()
```

Ключевые исправления:
- `SELECT ... FOR UPDATE` на строке `membership` — исключает параллельный
  инкремент `bonus_points` из двух потоков одновременно.
- `bonus_applied` на конкретной строке `penalties` — идемпотентность привязана
  к **событию** (этому конкретному улову), а не к абстрактному счётчику. Если
  worker падает после инкремента, но до коммита — вся транзакция откатывается
  целиком, повторный запуск снова увидит `bonus_applied = false` и корректно
  начислит один раз.
- Обе блокировки (`penalty` и `membership`) берутся в одной транзакции — устраняет
  как race condition, так и утечку бонусов при сбоях.

`bonus_points` хранится на **`users.id`**, а не на `membership_id` (уточнение из
ревью) — переживает выход из клуба, но сгорает автоматически через 90 дней
неактивности (cron-задача `expire_stale_bonus_points`).

```sql
ALTER TABLE users ADD COLUMN bonus_points INT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN bonus_points_updated_at TIMESTAMPTZ;
```

---

## Дополнительно закрыто: UI для штрафа без улова (п. 6 доп. замечаний)

```sql
ALTER TABLE penalties ADD COLUMN reason VARCHAR NOT NULL DEFAULT 'caught';
-- значения: 'caught' | 'window_closed_no_catch'
```

```python
async def close_catch_window_job():
    unresolved = await penalty_repo.get_unresolved_after_window()
    for violation in unresolved:
        await penalty_service.process_penalty(
            violation, catcher_membership_id=None, reason="window_closed_no_catch",
        )
```

UI-правило для экрана "Сегодня" у нарушителя:
- `reason = 'caught'` → "Тебя поймал участник {имя}, штраф {сумма} ушёл в фонд клуба."
- `reason = 'window_closed_no_catch'` → "Ты не отметился, штраф {сумма} автоматически
  ушёл в фонд клуба (никто не поймал)."

Оба случая одинаково списывают депозит — разница только в тексте объяснения,
что не даёт пользователю ощущения "несправедливости", даже если его не поймал
конкретный человек.

---

## Дополнительно закрыто: валидация суммы процентов в season_prize_rules

```python
async def validate_prize_rules(habit_id: UUID):
    rules = await prize_rules_repo.get_for_habit(habit_id)
    by_metric = defaultdict(float)
    for rule in rules:
        by_metric[rule.metric] += float(rule.percentage)
    for metric, total in by_metric.items():
        if abs(total - 100.0) > 0.01:
            raise InvalidPrizeRulesError(metric=metric, total=total)
```

Вызывается при сохранении правил в админке **и** повторно перед запуском
`close_season` — двойная проверка, чтобы правила нельзя было испортить ни на
этапе конфигурации, ни на этапе выплаты.

Дополнительно — версионирование правил через снапшот на старте сезона:

```sql
ALTER TABLE seasons ADD COLUMN prize_rules_snapshot JSONB;
```

При создании сезона текущие `season_prize_rules` копируются в
`prize_rules_snapshot` — если правила поменяются в середине сезона, `close_season`
использует снапшот, зафиксированный на старте, а не текущую версию.

---

## Итоговый статус готовности к разработке

| Блокер | Статус |
|---|---|
| Хостинг (ФЗ-152) | Закрыт — VPS в РФ на старте, план миграции на Yandex Cloud |
| Service-to-service auth | Закрыт — отдельный `/internal/` контур с short-lived JWT |
| Race condition в бонусах | Закрыт — `FOR UPDATE` + идемпотентность на уровне `penalty_id` |
| UI для штрафа без улова | Закрыт — поле `reason`, два текста в интерфейсе |
| Валидация призовых правил | Закрыт — проверка суммы % + снапшот на старте сезона |

Оставшиеся пункты из раздела "Важно" и "Можно улучшить" предыдущего ревью
(rate limit на /start, DR/бэкапы, мониторинг бизнес-аномалий, ADR-документация,
модерация чатов) переносятся в backlog первой итерации после MVP — они повышают
качество, но не блокируют запуск разработки при соблюдении зафиксированных выше
решений.
