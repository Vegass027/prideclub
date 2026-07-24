# 06 — Модель данных

> Snapshot от 2026-07-23. Схема таблиц и антифрод актуальны. **Список миграций
> расширен до 11** (000 → 011), см. §3. Финансовая идемпотентность — через
> уникальные индексы, см. §5. Topic-scoped чек-ины и третий топик для чата
> клуба — см. §6 (новый раздел).

Финальная схема БД, миграции, антифрод и идемпотентность. Все решения здесь — результат
6 итераций ревью, готовы к применению.

---

## 1. Основные таблицы

### users
| Поле | Тип | Описание |
|---|---|---|
| id | BIGINT | Telegram user_id |
| username | VARCHAR | @username (NULL возможен) |
| first_name | VARCHAR | Имя из Telegram |
| timezone | VARCHAR | Часовой пояс (для отображения, не для бизнес-логики) |
| bonus_points | BIGINT | Накопительные бонусы (переживают смену клубов) |
| bonus_points_updated_at | TIMESTAMPTZ | Для cron сгорания через 90 дней |
| notifications_enabled | BOOLEAN | Сделал ли пользователь `/start` боту |
| accepted_offer_at | TIMESTAMPTZ | Согласие с офертой (последней версии) |
| deleted_at | TIMESTAMPTZ | Право на удаление по ФЗ-152 |
| data_anonymized | BOOLEAN | Признак анонимизации |
| photo_file_id | VARCHAR(128) | Telegram file_id аватарки (Pravki.md §7.1, миграция 013). NULL = нет аватарки или worker `update_user_photos` ещё не подтянул. Endpoint `/api/v1/users/{id}/photo` → 404 если NULL |
| photo_fetched_at | TIMESTAMPTZ | Когда `update_user_photos` cron последний раз обновлял `photo_file_id` |
| created_at | TIMESTAMPTZ | |

### habits (клубы привычек)
| Поле | Тип | Описание |
|---|---|---|
| id | UUID | |
| title | VARCHAR | Название |
| chat_id | BIGINT | ID чата клуба в Telegram (Bot API-форма, для супергруппы = `-100<short_id>`) |
| checkin_window_start | TIME | Начало окна чек-ина |
| checkin_window_end | TIME | Конец окна чек-ина |
| timezone | VARCHAR | **TZ клуба** (не пользователя) — для расчёта "сегодня" |
| penalty_amount | INT | Размер штрафа |
| price_month | INT | Стоимость подписки |
| proof_type | ENUM | `video_note` / `photo` / `text` — **алиас** `proof_types[0]`, обновляется синхронно в `HabitService.update` |
| proof_types | JSONB NOT NULL | **Массив 1..3 значений** ∈ {video_note, photo, text} (миграция 012). CHECK constraint в БД — структурный; значения валидируются в `HabitService._validate_proof_types` |
| prize_pool | INT | Текущий призовой фонд клуба |
| checkin_topic_thread_id | BIGINT NULL | `message_thread_id` топика форума для чек-инов (миграция 010) |
| notifications_topic_thread_id | BIGINT NULL | `message_thread_id` топика форума для уведомлений о ловле и штрафах (миграция 010) |
| chat_topic_thread_id | BIGINT NULL | `message_thread_id` топика форума для общего чата участников (миграция 011) |

**Правило:** "сегодня" и дедлайн считаются в TZ клуба, а не пользователя. Это устраняет
путаницу при переезде и делает дедлайн одинаковым для всех участников клуба.

**Topic-scoped (миграции 010, 011).** Для супергруппы с включённым режимом форумов бот
принимает чек-ины только из топика `checkin_topic_thread_id`. Сообщения из General или
других топиков получают код `not_checkin_topic` (HTTP 422) и не влияют на бизнес-логику.
См. §6.

### memberships
| Поле | Тип | Описание |
|---|---|---|
| id | UUID | |
| user_id | BIGINT | FK → users |
| habit_id | UUID | FK → habits |
| status | ENUM | `active` / `paused` / `left` |
| deposit_balance | BIGINT | Текущий баланс депозита |
| subscription_until | DATE | Дата окончания оплаченного периода |
| auto_renew_enabled | BOOLEAN | Автопродление подписки |
| joined_at | TIMESTAMPTZ | |

### checkins
| Поле | Тип | Описание |
|---|---|---|
| id | UUID | |
| membership_id | UUID | FK → memberships |
| date | DATE | Дата чек-ина |
| status | ENUM | `done` / `missed` |
| proof_message_id | BIGINT | ID сообщения-доказательства в чате |
| verified_at | TIMESTAMPTZ | |

**Уникальный индекс:** `(membership_id, date)` — один чек-ин в сутки, идемпотентно.

### penalties
| Поле | Тип | Описание |
|---|---|---|
| id | UUID | |
| membership_id | UUID | FK → memberships (кто нарушил) |
| catcher_membership_id | UUID | FK → memberships (кто спалил, NULL если никто) |
| amount | INT | Сумма штрафа |
| fund_share | INT | Сумма в призовой фонд (= amount) |
| catcher_bonus_points | INT | Бонусы охотнику |
| reason | VARCHAR | `caught` / `window_closed_no_catch` |
| bonus_applied | BOOLEAN | Начислен ли бонус за этот улов |
| date | DATE | Для уникального индекса |
| idempotency_key | VARCHAR UNIQUE | `penalty:{membership_id}:{date}` |
| created_at | TIMESTAMPTZ | |

**Уникальный индекс:** `(membership_id, date, reason)` — один штраф в день на участника
по каждой причине.

### transactions
| Поле | Тип | Описание |
|---|---|---|
| id | UUID | |
| user_id | BIGINT | FK → users |
| type | VARCHAR | `subscription` / `deposit_topup` / `deposit_withdraw` / `penalty` / `prize` / `bonus_catch` / `bonus_subscription` / `bonus_points` |
| amount | INT | Может быть отрицательным |
| balance_after | BIGINT | Баланс депозита после операции (для аудита) |
| related_penalty_id | UUID | FK → penalties |
| related_membership_id | UUID | FK → memberships |
| idempotency_key | VARCHAR UNIQUE | `telegram_payment_charge_id` для платежей |
| created_at | TIMESTAMPTZ | |

`idempotency_key` для платежей строится из `telegram_payment_charge_id` — повторная
доставка webhook не создаст задвоенное начисление.

### seasons
| Поле | Тип | Описание |
|---|---|---|
| id | UUID | |
| habit_id | UUID | FK → habits |
| starts_at | DATE | |
| ends_at | DATE | |
| prize_pool | INT | Призовой фонд сезона |
| prize_rules_snapshot | JSONB | Снапшот правил на момент старта |
| status | VARCHAR | `active` / `closed` / `paid_out` |

### season_stats
| Поле | Тип | Описание |
|---|---|---|
| season_id | UUID | FK → seasons |
| membership_id | UUID | FK → memberships |
| streak_days | INT | Текущий стрик в сезоне |
| total_penalties_caught | INT | Уловы в сезоне |
| total_penalties_received | INT | Нарушения в сезоне |

PRIMARY KEY: `(season_id, membership_id)`.

`checkins`, `penalties`, `transactions` — **исторические**, не обнуляются. `season_stats`
— рабочий срез, пересчитывается инкрементально.

---

## 2. Вспомогательные таблицы

### pricing_rules
```sql
id, habit_rank INT, price_month INT, active_from TIMESTAMPTZ, active_to TIMESTAMPTZ
```
Цена за N-ю привычку читается из БД, не из кода — A/B-тесты без релиза.

### suspicious_pairs
```sql
membership_id_a UUID, membership_id_b UUID,
reason VARCHAR, -- same_day_signup / same_referrer / mutual_avoidance
detected_at TIMESTAMPTZ, status VARCHAR -- flagged / cleared / banned
```
PRIMARY KEY: `(membership_id_a, membership_id_b)`.

**Автоматическое поведение при `status = 'flagged'`:**
- Штраф нарушителя списывается как обычно (дисциплина не ослабляется).
- `catcher_bonus_points` для этой пары **не начисляется**, улов не идёт в лидерборд.
- Пользователи **не уведомляются** о метке.

Администратору доступны два действия: `cleared` (бонусы включаются) / `banned`
(membership → paused).

### bonus_rules
```sql
event_type VARCHAR, -- catch / streak_7 / streak_30
threshold INT,
reward_type VARCHAR, -- subscription_days / points / priority
reward_value INT
```

### season_prize_rules
```sql
habit_id UUID, rank_from INT, rank_to INT,
metric VARCHAR, -- streak / catches
percentage NUMERIC(5,2)
```
UNIQUE: `(habit_id, metric, rank_from, rank_to)`. Применяется правило валидации:
сумма `percentage` по каждой `metric` должна быть 100%.

### daily_streak_snapshots
```sql
membership_id UUID, date DATE, streak_days INT
PRIMARY KEY (membership_id, date)
```
Пишется каждый день в cron `close_catch_window`. Используется для дешёвого расчёта
конверсии "1-й чек-ин → 7 дней подряд".

### offer_versions
```sql
id UUID, version VARCHAR, effective_from TIMESTAMPTZ, document_url TEXT
```

### user_consents
```sql
user_id BIGINT, offer_version_id UUID, accepted_at TIMESTAMPTZ, ip_address INET
PRIMARY KEY (user_id, offer_version_id)
```

---

## 3. Полный набор миграций

> На проде применена голова `013_user_photo` (см.
> [09-prod-readiness.md](09-prod-readiness.md) §1.1). Файлы миграций лежат в
> `apps/backend/alembic/versions/`. Для upgrade/downgrade используется
> `make migrate` / `make migrate-test`.

| # | Файл | Что делает |
|---|---|---|
| `000_extensions` | `000_extensions.py` | `pgcrypto`, `pg_stat_statements` |
| `001_initial_schema` | `001_initial_schema.py` | Все таблицы раздела 1 + правильные типы и индексы |
| `002_bonus_and_penalty_fixes` | `002_bonus_and_penalty_fixin.py` | `penalties.{bonus_applied, reason, date}` + UNIQUE `(membership_id, date, reason)`, `users.bonus_points`, `memberships.auto_renew_enabled`, `seasons.prize_rules_snapshot`, таблицы `daily_streak_snapshots`, `suspicious_pairs`, `bonus_rules`, `season_prize_rules`, `pricing_rules`, `offer_versions`, `user_consents` |
| `003_migrate_bonus_points` | `003_migrate_bonus_points.py` | Sanity-check + перенос `memberships.bonus_points` → `users.bonus_points` |
| `004_notifications_and_offer` | `004_notifications_and_offer.py` | `users.notifications_enabled`, `accepted_offer_at`, `deleted_at`, `data_anonymized`; `habits.timezone='Europe/Moscow'` default |
| `005_users_gdpr_columns` | `005_users_gdpr_columns.py` | GDPR-специфичные колонки (расширение 004) |
| `006_suspicious_pairs_index` | `006_suspicious_pairs_index.py` | Доп. индексы по `suspicious_pairs` для антифрод-эвристик |
| `007_habit_admin_fields` | `007_habit_admin_fields.py` | Поля для Admin Mini App: `habits.photo_url`, `habits.telegram_invite_link`, `habits.member_limit`, `habits.curator_id`, `habits.stat_*` |
| `008_character_and_club_fields` | `008_character_and_club_fields.py` | `habits.stat_name`, `habits.stat_icon`, `habits.stat_gain_per_checkin`, `habits.stat_loss_per_miss` — характеристики и персонаж (TZ) |
| `009_chat_id_partial_unique` | `009_chat_id_partial_unique.py` | Частичный UNIQUE-индекс на `habits.chat_id` (только `WHERE chat_id IS NOT NULL`) — гарантирует, что у двух активных клубов не может быть одного Telegram-чата |
| `010_habit_topics` | `010_habit_topics.py` | `habits.checkin_topic_thread_id` и `habits.notifications_topic_thread_id` (BIGINT NULL) + partial btree-индексы. Включает топик-фильтр чек-инов. |
| `011_habit_chat_topic` | `011_habit_chat_topic.py` | `habits.chat_topic_thread_id` (BIGINT NULL) + partial btree-индекс. Третий топик для общего чата участников клуба. |
| `012_proof_types` | `012_proof_types.py` | `habits.proof_types JSONB NOT NULL DEFAULT '["video_note"]'` + CHECK (длина 1..3) + GIN-индекс. Бэкфилл: `UPDATE habits SET proof_types = jsonb_build_array(proof_type::text)`. `habits.proof_type` остаётся как алиас первого элемента массива для обратной совместимости. |
| `013_user_photo` | `013_user_photo.py` | `users.photo_file_id VARCHAR(128) NULL` + `users.photo_fetched_at TIMESTAMPTZ NULL` + partial index (`photo_file_id IS NOT NULL`). Для Pravki.md §7.1: подход C' — backend делает 307 redirect на Telegram CDN через endpoint `/api/v1/users/{id}/photo`. file_id постоянный (см. telegram FAQ), `bot.getFile` результат кэшируется в Redis 6ч. |

> SQL-блоки 000–004 в старой версии этого документа устарели (не отражают 005–009).
> Реальный код — в `apps/backend/alembic/versions/`.

---

## 4. Антифрод

### 4.1. Один чек-ин в сутки
Уникальный индекс `(membership_id, date)` в `checkins`. Повторная попытка в тот же день
идемпотентно возвращает существующую запись.

### 4.2. Валидация медиа
```python
def validate_proof_media(message: Message, proof_type: ProofType) -> tuple[bool, str | None]:
    if proof_type == ProofType.VIDEO_NOTE:
        if message.video_note is None:
            return False, "wrong_type"
        if message.video_note.duration < 3:
            return False, "too_short"
    elif proof_type == ProofType.PHOTO:
        if not message.photo:
            return False, "wrong_type"
    elif proof_type == ProofType.TEXT:
        if not (message.text and len(message.text.strip()) > 0):
            return False, "empty"
    if message.forward_date is not None:
        return False, "forwarded"
    return True, None
```
Дополнительно проверяется `message.date` — сообщение должно быть отправлено сейчас.

### 4.3. Rate limit на "Спалить"
```python
RATE_LIMIT_CATCH = "10/10s"  # в core/constants.py

async def check_catch_rate_limit(catcher_user_id: int, redis: Redis):
    key = f"catch_rate:{catcher_user_id}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 10)
    if count > 10:
        raise TooManyCatchAttemptsError()
```

### 4.4. Окно спаливания
**Окно "спаливания" = окно чек-ина клуба + 1 час после закрытия.** Все нарушители
видны всем участникам одновременно.

```python
def catch_window_end(self, club_date: date) -> datetime:
    window_end = datetime.combine(club_date, self.checkin_window_end, tzinfo=ZoneInfo(self.timezone))
    return window_end + timedelta(hours=1)
```

После `catch_window_end` cron `close_catch_window` фиксирует штрафы без улова через
`INSERT ... ON CONFLICT DO NOTHING`:

```sql
INSERT INTO penalties (id, membership_id, catcher_membership_id, reason, amount,
                       fund_share, date, created_at)
VALUES (gen_random_uuid(), :membership_id, NULL, 'window_closed_no_catch',
        :amount, :amount, CURRENT_DATE, now())
ON CONFLICT (membership_id, date, reason) DO NOTHING
RETURNING id;
```

Самоисключение: `catcher_membership_id != violator_membership_id` — инвариант на уровне
сервиса до похода в БД.

### 4.5. Защита от сговора
`suspicious_pairs` — автоматическое смягчение (штраф списывается, бонус не начисляется,
лидерборд не обновляется для пары). Администратор раз в неделю просматривает список
и принимает решение `cleared` / `banned`.

---

## 5. Race conditions и идемпотентность

### 5.1. Блокировка строки при списании депозита
```python
async def apply_penalty(self, membership_id: UUID, amount: int) -> Membership:
    async with self._db.begin():
        membership = await self._db.execute(
            select(Membership).where(Membership.id == membership_id).with_for_update()
        )
        membership = membership.scalar_one()

        if membership.deposit_balance < amount:
            amount = membership.deposit_balance
            membership.status = MembershipStatus.PAUSED

        membership.deposit_balance -= amount
        await self._db.flush()
        return membership
```
`SELECT ... FOR UPDATE` исключает параллельное списание в минус.

### 5.2. Транзакционная целостность штрафа
Все три записи (списание, начисление в фонд, `penalties`) — в **одной транзакции**:

```python
async def process_penalty(self, checkin_miss, catcher_membership_id):
    idempotency_key = f"penalty:{checkin_miss.membership_id}:{checkin_miss.date}"

    async with self._db.begin():
        existing = await self._penalty_repo.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing  # идемпотентность

        membership = await self._membership_repo.lock_for_update(checkin_miss.membership_id)
        amount = min(membership.deposit_balance, self._habit.penalty_amount)

        membership.deposit_balance -= amount
        await self._fund_repo.add_to_pool(self._habit.id, amount)

        penalty = await self._penalty_repo.create(
            membership_id=membership.id,
            catcher_membership_id=catcher_membership_id,
            amount=amount,
            idempotency_key=idempotency_key,
        )
        await self._transaction_repo.create(
            user_id=membership.user_id,
            type=TransactionType.PENALTY,
            amount=-amount,
            related_penalty_id=penalty.id,
            balance_after=membership.deposit_balance,
        )
        return penalty
```

### 5.3. Идемпотентность бонусов
```python
async def apply_catch_bonus(self, catcher_membership_id: UUID, penalty_id: UUID):
    async with self._db.begin():
        penalty = await self._penalty_repo.lock_for_update(penalty_id)
        if penalty.bonus_applied:
            return  # уже начислено

        membership = await self._membership_repo.lock_for_update(catcher_membership_id)
        membership.bonus_points += 1

        rule = await self._bonus_rules_repo.get("catch", threshold=5)
        if membership.bonus_points % rule.threshold == 0:
            await self._grant_reward(membership, rule)

        penalty.bonus_applied = True
        await self._db.flush()
```

**Правило:** если включено автопродление подписки — бонусные дни не продлевают дату,
а конвертируются в `bonus_points` (накопительные), чтобы не задвоить оплату.

---

## 6. Topic-scoped чек-ины (forum supergroups)

> Реализовано в миграциях 010 (`checkin_topic_thread_id`,
> `notifications_topic_thread_id`) и 011 (`chat_topic_thread_id`).

### 6.1. Три топика клуба в одной супергруппе

Каждый клуб, живущий в супергруппе с включённым режимом форума (топиков),
использует **три топика**:

| Топик | Поле | Назначение |
|---|---|---|
| Чек-ины | `checkin_topic_thread_id` | Сюда участники отправляют видео-кружки / фото / текст. Бот принимает только сообщения из этого топика. |
| Уведомления | `notifications_topic_thread_id` | Сюда бот публикует события ловли (`👨🏽‍🦰 X словил(а) 👨🏽‍🦰 Y, 💸 N ₽`) и штрафов за пропуск (`⏰ Окно закрыто`). |
| Чат клуба | `chat_topic_thread_id` | Общая переписка участников. Кнопка «💬 Перейти в чат» в User Mini App открывает именно этот топик. |

Все три `thread_id` хранятся в **Bot API-форме** `int` (не строкой), для супергруппы
`chat_id` в БД равен `-100<short_id>`. Парсер в `app/core/telegram_links.py` принимает
обе формы ссылок (`https://t.me/c/<short_id>/<thread>` и `https://t.me/c/-100.../<thread>`)
и нормализует в Bot API-форму.

### 6.2. Правила валидации (HabitService)

При создании/редактировании клуба:

1. **Все три ссылки обязательны** в `create()`; в `update()` — опциональные.
2. `chat_id` в каждой топик-ссылке должен совпадать с `habits.chat_id`.
3. `thread_id` всех трёх топиков должны различаться (код `habit_topics_must_differ`).
4. Никакая пара `(chat_id, thread_id)` не должна быть привязана к другому клубу (код `habit_topic_duplicate`).
5. В `update()` если `habit.chat_id == 0`, первая валидная топик-ссылка автоматически привязывает чат.

### 6.3. Антифрод: фильтр по топику

В `CheckinService.process_checkin` (после миграции 010):

```
if (habit.checkin_topic_thread_id is not None
    and message_thread_id != habit.checkin_topic_thread_id):
    raise CheckinWrongTopicError()
```

`message_thread_id` приходит из payload бота (`aiogram` `Message.message_thread_id`,
None для General). Бот прокидывает поле в `/internal/checkins/process` → Celery → backend.

Результат: сообщения из чужих топиков (или из General при заданном топике чек-инов)
отбрасываются с кодом `not_checkin_topic` (HTTP 422), без побочных эффектов.

### 6.4. Публикация уведомлений о ловле

`apps/backend/app/services/notification_service.py` отправляет сообщения в топик
`notifications_topic_thread_id` через прямой HTTP-запрос к Telegram Bot API
(`aiohttp.ClientSession`, без импорта `aiogram`). Best-effort: ошибки сети логируются,
бизнес-флоу не падает.

Вызывается:
- в `worker/tasks/process_penalty.py` после успешного `apply_catch` и `commit()`;
- в `worker/tasks/close_catch_window.py` после `apply_window_expired`.

### 6.5. UX в Mini App

- **Admin Mini App** (`HabitCreatePage`, `HabitEditForm`): три поля для ссылок на топики.
  Валидация формата + дедупликация. Плейсхолдеры «Вставь ссылку на топик» (без шаблона).
- **User Mini App**: три кнопки на странице клуба (`TodayPage`) и в карточках клубов.
  «🎬 Сделать чек-ин» → `openCheckinTopic(chat_id, checkin_topic_thread_id)`;
  «💬 Перейти в чат» → `openCheckinTopic(chat_id, chat_topic_thread_id)`;
  «❤️ Вы состоите в клубе» (disabled) или «👋 Присоединиться к клубу» в секции
  «Клуб в Telegram» в зависимости от `membership.status`.
- **Доменный URL** формируется как `https://t.me/c/<short_chat_id>/<thread_id>` —
  Telegram не принимает `-100<short_id>` в ссылках, только короткий id.
  `buildTopicLink` в `apps/frontend/src/shared/telegram/topicLink.ts` отбрасывает `-100`.

### 6.6. Откат

Миграции 010 и 011 additive-only: `downgrade()` просто дропает колонки и индексы.
Перед rollback стоит убедиться, что в колонках нет ценных данных (для существующих
клубов до миграции все три `thread_id` остаются NULL — режим «без топиков»).

---

## 7. BonusService

```python
class BonusService:
    def __init__(self, membership_repo, transaction_repo, bonus_rules_repo, user_repo):
        self._membership_repo = membership_repo
        self._transaction_repo = transaction_repo
        self._bonus_rules_repo = bonus_rules_repo
        self._user_repo = user_repo

    async def apply_catch_bonus(self, catcher_membership_id: UUID, penalty_id: UUID):
        # ... (см. п. 5.3)
        pass

    async def _grant_reward(self, membership, rule):
        user = await self._user_repo.get(membership.user_id)
        if membership.auto_renew_enabled:
            # Автоподписка покрывает продление — копим в points
            user.bonus_points += rule.reward_value
            await self._transaction_repo.create(
                user_id=user.id, type="bonus_points",
                amount=0, related_membership_id=membership.id,
            )
        else:
            membership.subscription_until += timedelta(days=rule.reward_value)
            await self._transaction_repo.create(
                user_id=user.id, type="bonus_subscription",
                amount=0, related_membership_id=membership.id,
                balance_after=membership.deposit_balance,
            )
```

`bonus_points` хранится на **`users.id`** (не на membership) — переживает выход из клуба,
сгорает через 90 дней неактивности (cron `expire_stale_bonus_points`).
Уведомление за 7 дней до сгорания — через бота (если `notifications_enabled = true`).

---

## 8. Сезоны и призы

### Распределение призов
Полностью автоматическое через cron `close_season` по `prize_rules_snapshot`:

```python
async def close_season(season_id: UUID):
    async with db.begin():
        season = await season_repo.lock_for_update(season_id)
        rules = season.prize_rules_snapshot  # снапшот на момент старта

        for rule in rules:
            members = await season_stats_repo.get_ranked(
                season_id, rule["metric"], rule["rank_from"], rule["rank_to"]
            )
            amount_per_member = int(season.prize_pool * rule["percentage"] / 100 / len(members))
            for m in members:
                await prize_repo.create(season_id, m.membership_id, amount_per_member)

        season.status = "closed"
```

Администратор только запускает выплату (`paid_out_at`) вручную после проверки списка.

### Валидация правил
Двойная проверка (на сохранении в админке + перед `close_season`):

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

### Пример конфигурации призов

| metric | rank | percentage |
|---|---|---|
| streak | 1 | 40% |
| streak | 2 | 20% |
| streak | 3 | 10% |
| catches | 1 | 20% |
| catches | 2 | 10% |

---

## 9. Cron-задачи

| Задача | Расписание | Действие |
|---|---|---|
| `close_catch_window` | Per-habit в `checkin_window_end + 1h` | INSERT штрафов без улова, обновление streak, снапшот |
| `expire_stale_bonus_points` | Ежедневно | Сгорание бонусов старше 90 дней |
| `notify_bonus_expiring` | Ежедневно за 7 дней до сгорания | Уведомления в Telegram |
| `close_season` | В `season.ends_at` | Распределение призов |
| `integrity_check_bonus_transactions` | Ежедневно | Алерт если `bonus_applied=true` без связанной транзакции |
| `heartbeat_backup_check` | Внешний, ежечасно | Алерт если `heartbeat/last_success.txt` старше 26 часов |

---

## 10. ФЗ-152

- **Перечень ПДн:** Telegram `user_id`, `username`, `first_name` — фиксируется в политике.
- **Согласие:** при первом платеже через `showConfirm`, версия оферты логируется в
  `user_consents` с `ip_address`.
- **Право на удаление:** команда `/delete_my_data` или кнопка в профиле → анонимизация
  (`user_deleted = true`, `username = NULL`).
- **Финансовая история** (`transactions`, `penalties`) сохраняется в анонимизированном
  виде — требования бухгалтерского/налогового учёта имеют приоритет.
- **Срок хранения:** активность + 3 года после последнего платежа, затем архив.
- **Хранение:** исключительно на территории РФ (Selectel VPS).
