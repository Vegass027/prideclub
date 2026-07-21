# Финальные доработки v2 — production-ready спецификация

Документ закрывает пункты 1–6 из раздела "Критично" второго ревью, а также пункты
17, 18, 22 из "критически отсутствует" — они помечены как блокирующие до старта
кодирования. Дополняет kriticheskie_dorabotki_review.md.

---

## 1. Упрощённая модель "окна спаливания" (заменяет п. 3.5)

Принята рекомендация — единая понятная модель без противоречий между документами.

**Правило:** окно "спаливания" = окно чек-ина клуба + 1 час после его закрытия.
Все нарушители видны **всем** участникам клуба одновременно, без случайной подвыборки
30%. Защита от сговора переносится полностью на эвристику (см. п. 2 ниже) и лимит
"один и тот же охотник не может спалить одного и того же нарушителя больше N раз
за сезон подряд" — вместо усложнения видимости.

```python
class Habit:
    def catch_window_end(self, club_date: date) -> datetime:
        window_end = datetime.combine(club_date, self.checkin_window_end, tzinfo=ZoneInfo(self.timezone))
        return window_end + timedelta(hours=1)

    def is_catchable(self, membership_status: str, now: datetime, club_date: date) -> bool:
        return membership_status == "missed" and now <= self.catch_window_end(club_date)
```

После `catch_window_end` статус нарушителя автоматически фиксируется как "обработан
без улова" (штраф всё равно уходит в фонд через cron `close_catch_window`, просто без
бонуса охотнику) — никакого "зависания" статуса.

Самоисключение: `catcher_membership_id != violator_membership_id` — проверяется как
инвариант на уровне сервиса, до похода в БД.

```python
class PenaltyService:
    async def catch_violator(self, catcher_membership_id: UUID, violator_membership_id: UUID):
        if catcher_membership_id == violator_membership_id:
            raise CannotCatchSelfError()
        ...
```

---

## 2. Автоматическое смягчение при подозрении на сговор (заменяет п. 3.4)

Ручная модерация убирается из основного потока — остаётся только как опциональное
действие администратора над уже автоматически обработанными случаями.

```sql
CREATE TABLE suspicious_pairs (
    membership_id_a UUID NOT NULL REFERENCES memberships(id),
    membership_id_b UUID NOT NULL REFERENCES memberships(id),
    reason VARCHAR NOT NULL,           -- same_day_signup / same_referrer / mutual_avoidance
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status VARCHAR NOT NULL DEFAULT 'flagged',  -- flagged / cleared / banned
    PRIMARY KEY (membership_id_a, membership_id_b)
);
```

Автоматическое поведение при `status = 'flagged'`:
- Штраф нарушителя **списывается как обычно** (дисциплина не ослабляется).
- `catcher_bonus_points` для этой пары **не начисляется**, улов не идёт в лидерборд
  "Охотники".
- Пользователи не уведомляются о том, что помечены — чтобы не провоцировать эскалацию
  или попытки обойти детекцию.

Администратору в админке доступны только два действия над записью: **"Разморозить"**
(status → cleared, бонусы включаются обратно) или **"Забанить"** (status → banned,
membership переводится в paused). Никакого "расследования" — решение принимается по
готовому списку раз в неделю, что масштабируется даже при тысячах участников.

---

## 3. Правила распределения сезонных призов (заменяет ручной процесс п. 7)

```sql
CREATE TABLE season_prize_rules (
    id UUID PRIMARY KEY,
    habit_id UUID NOT NULL REFERENCES habits(id),
    rank_from INT NOT NULL,        -- например, 1
    rank_to INT NOT NULL,          -- например, 1 (только топ-1) или 2-3 (диапазон)
    metric VARCHAR NOT NULL,       -- 'streak' | 'catches'
    percentage NUMERIC(5,2) NOT NULL,  -- доля призового фонда
    UNIQUE (habit_id, metric, rank_from, rank_to)
);
```

Пример конфигурации (задаётся один раз при создании клуба, не требует ручного решения
каждый сезон):

| metric | rank | percentage |
|---|---|---|
| streak | 1 | 40% |
| streak | 2 | 20% |
| streak | 3 | 10% |
| catches | 1 | 20% |
| catches | 2 | 10% |

Распределение выполняется автоматически cron-задачей `close_season`:

```python
async def close_season(season_id: UUID):
    async with db.begin():
        season = await season_repo.lock_for_update(season_id)
        rules = await prize_rules_repo.get_for_habit(season.habit_id)
        for rule in rules:
            members = await season_stats_repo.get_ranked(season_id, rule.metric, rule.rank_from, rule.rank_to)
            amount_per_member = int(season.prize_pool * rule.percentage / 100 / len(members))
            for m in members:
                await prize_repo.create(season_id, m.membership_id, amount_per_member)
        season.status = "closed"
```

Администратор только запускает выплату (`paid_out_at`) вручную после проверки списка —
сам расчёт полностью автоматический.

---

## 4. BonusService — реализация начисления бонусных дней (заменяет п. 1 частично)

```sql
CREATE TABLE bonus_rules (
    id UUID PRIMARY KEY,
    event_type VARCHAR NOT NULL,      -- 'catch' | 'streak_7' | 'streak_30'
    threshold INT NOT NULL,           -- например, 5 уловов
    reward_type VARCHAR NOT NULL,     -- 'subscription_days' | 'points' | 'priority'
    reward_value INT NOT NULL
);

ALTER TABLE memberships ADD COLUMN bonus_points INT NOT NULL DEFAULT 0;
```

```python
class BonusService:
    def __init__(self, membership_repo, transaction_repo, bonus_rules_repo):
        self._membership_repo = membership_repo
        self._transaction_repo = transaction_repo
        self._bonus_rules_repo = bonus_rules_repo

    async def apply_catch_bonus(self, membership_id: UUID):
        membership = await self._membership_repo.get(membership_id)
        membership.bonus_points += 1

        rule = await self._bonus_rules_repo.get("catch", threshold=5)
        if membership.bonus_points % rule.threshold == 0:
            if membership.auto_renew_enabled:
                # автоподписка уже покрывает продление — бонус копится в points,
                # не продлевает дату, чтобы не задвоить оплату
                await self._transaction_repo.create(
                    user_id=membership.user_id, type="bonus_points",
                    amount=0, related_membership_id=membership.id,
                )
            else:
                membership.subscription_until += timedelta(days=rule.reward_value)
                await self._transaction_repo.create(
                    user_id=membership.user_id, type="bonus_subscription",
                    amount=0, related_membership_id=membership.id,
                    balance_after=membership.deposit_balance,
                )
        await self._membership_repo.save(membership)
```

Правило зафиксировано явно: **если включено автопродление подписки — бонусные дни не
продлевают дату, а конвертируются в накопительные `bonus_points`**, которые можно
потратить отдельно (например, на след. привычку) — это устраняет риск задвоения оплаты.

---

## 5. UX-правило "кто может спалить" (уточнение к п. 4.2 архитектуры)

Зафиксировано явно, синхронно с уникальным индексом `(membership_id, created_at::date)`:

> Кнопка "Спалить" доступна всем участникам клуба, пока штраф по нарушителю не оформлен.
> Как только первый пользователь успешно нажимает "Спалить" — статус нарушителя
> меняется на "обработан", и кнопка **немедленно исчезает у всех остальных** (через
> обновление статуса в Redis-кэше, который читает Mini App).

```python
# при попытке повторного "спалить" после того, как штраф уже создан:
class PenaltyAlreadyProcessedError(DomainError):
    status_code = 409
    code = "penalty_already_processed"
```

Frontend по коду `penalty_already_processed` показывает тост "Кто-то уже поймал этого
участника" вместо технической ошибки — ожидаемый, а не "сломанный" UX.

---

## 6. Корректный расчёт конверсии "1-й чек-ин → 7 дней подряд"

Принята рекомендация — считать напрямую из `checkins`, не из текущего `season_stats`,
плюс добавляется таблица снапшотов для быстрой аналитики без дорогих запросов на
больших объёмах:

```sql
CREATE TABLE daily_streak_snapshots (
    membership_id UUID NOT NULL REFERENCES memberships(id),
    date DATE NOT NULL,
    streak_days INT NOT NULL,
    PRIMARY KEY (membership_id, date)
);
```

Снапшот пишется каждый день cron-задачей `close_checkin_window` (уже существует в
архитектуре) — на основе текущего значения `streak_days`, без пересчёта истории.
Конверсионный запрос становится дешёвым:

```sql
SELECT
  count(*) FILTER (WHERE max_streak >= 7) * 1.0 / count(*) AS conversion
FROM (
  SELECT membership_id, max(streak_days) AS max_streak
  FROM daily_streak_snapshots
  GROUP BY membership_id
) m;
```

---

## 7. Валидация initData (закрывает п. 17 — блокирующий пункт безопасности)

Без этой проверки любой клиент может подделать `user_id` в запросе к backend API.
Обязательная проверка на **каждый** запрос к Backend API от Mini App.

```python
import hashlib, hmac
from urllib.parse import parse_qsl

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

    return parsed  # содержит user (json), auth_date и т.д.
```

Middleware backend'а вызывает эту функцию для **каждого** запроса и извлекает
`user_id` только из проверенных данных, никогда из тела запроса напрямую:

```python
@app.middleware("http")
async def telegram_auth_middleware(request: Request, call_next):
    init_data = request.headers.get("X-Telegram-Init-Data")
    validated = validate_init_data(init_data, settings.BOT_TOKEN)
    request.state.telegram_user = json.loads(validated["user"])
    return await call_next(request)
```

**Правило проекта: ни один эндпоинт не принимает `user_id` как параметр запроса —
только из `request.state.telegram_user`, установленного middleware после проверки
подписи.**

---

## 8. Минимальное соответствие ФЗ-152 (закрывает п. 18)

- **Перечень ПДн, которые хранятся**: Telegram `user_id`, `username`, `first_name` —
  фиксируется в политике обработки персональных данных (публикуется в Mini App и/или
  на сайте).
- **Согласие на обработку ПДн** собирается вместе с согласием на оферту (см. таблицы
  `offer_versions` / `user_consents` из предыдущего документа) — единый экран согласия
  при первом платеже фиксирует оба типа согласия.
- **Право на удаление**: команда `/delete_my_data` в боте и кнопка в Mini App
  (раздел "Профиль") запускают процесс:
  - Персональные данные (username, first_name) анонимизируются (`user_deleted = true`,
    `username = NULL`).
  - Финансовая история (`transactions`, `penalties`) **сохраняется** в анонимизированном
    виде (по `user_id`, без имени) — требование бухгалтерского/налогового учёта имеет
    приоритет над правом на удаление в части финансовых записей.
- **Срок хранения**: данные хранятся всё время активности аккаунта + 3 года после
  последнего платежа (стандартный срок для финансовых документов), затем архивируются.

```sql
ALTER TABLE users ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN data_anonymized BOOLEAN NOT NULL DEFAULT false;
```

- **Хранение персональных данных на территории РФ** — обязательное требование
  152-ФЗ: хостинг основной БД должен быть в РФ-юрисдикции (важно учитывать при выборе
  Railway/Render — при росте потребуется миграция на российский хостинг или VPS
  с размещением в РФ).

---

## 9. Тест-план критических сценариев (закрывает п. 22)

Обязательные интеграционные тесты до релиза MVP — без них поведение под нагрузкой
и при сбоях непредсказуемо.

| № | Сценарий | Ожидаемый результат |
|---|---|---|
| 1 | Два пользователя одновременно жмут "Спалить" одного нарушителя | Только один штраф создан, второй получает `penalty_already_processed` |
| 2 | Списание штрафа при депозите меньше суммы штрафа | Списывается остаток, `deposit_balance = 0`, статус → `paused` |
| 3 | Повторная доставка webhook `successful_payment` от Telegram | Транзакция не задвоена благодаря `idempotency_key` |
| 4 | Worker падает между списанием депозита и записью в `penalties` | При перезапуске транзакция откатывается целиком (атомарность), повторная задача не задваивает штраф |
| 5 | Пользователь пересылает старый кружок (forward) как доказательство | Чек-ин отклонён с кодом `FORWARDED` |
| 6 | Повторный чек-ин в тот же день | Возвращается существующая запись, дубликат не создаётся |
| 7 | Запрос к API с поддельным/устаревшим initData | 401, доступ отклонён на уровне middleware |
| 8 | Пользователь пытается "спалить" сам себя | `CannotCatchSelfError`, запрос отклонён до похода в БД |
| 9 | Suspicious pair пытается взаимно "ловить" друг друга | Штраф списывается, бонус не начисляется, лидерборд не обновляется для пары |
| 10 | Массовый поток чек-инов в 07:00 (нагрузочный тест) | p95 обработки < 30 сек, очередь не теряет сообщения |

Тесты 1–6 и 8–9 — юнит/интеграционные (pytest + тестовая БД), тест 7 — на уровне
middleware, тест 10 — нагрузочный (Locust/k6), запускается отдельно перед публичным
релизом.

---

## 10. Обновлённый порядок разработки MVP (финальная версия)

1. Валидация `initData` (п. 7) и правовая база ФЗ-152/оферта (п. 8) — до первой строки
   бизнес-логики, это инфраструктурный фундамент безопасности.
2. Схема БД целиком: `suspicious_pairs`, `season_prize_rules`, `bonus_rules`,
   `daily_streak_snapshots`, `offer_versions`, `user_consents`.
3. Backend: чек-ины с антифродом + единая модель окна спаливания (п. 1).
4. Backend: штрафы с блокировками, идемпотентностью, автоматическим смягчением
   suspicious pairs (п. 2).
5. BonusService (п. 4) и season closing job с автоматическим распределением призов (п. 3).
6. Frontend: UX-обработка `penalty_already_processed` и статусов "спалить" (п. 5).
7. Тест-план (п. 9) — прогоняется как часть CI перед каждым релизом, не только перед
   MVP.
8. Runbook для админа: алерты в Telegram при падении bot/worker, список
   suspicious_pairs на разморозку/бан, запуск выплаты призов.

Все пункты, помеченные ревьюером как блокирующие "до начала кодирования", закрыты
конкретными схемами и кодом выше — документ готов как основа для технического
задания разработчикам.
