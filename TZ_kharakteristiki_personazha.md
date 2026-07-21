# Техническое задание
## Модуль «Персонаж и характеристики» (геймификация привычек)

Версия: 2.1 (статус актуализирован)
Дата: 21.07.2026 22:30 CEST
Базируется на: `docs/01-concept.md`, `docs/06-data-model.md`, `docs/04-code-standards.md`, `AGENTS.md`

> **Принцип:** фича встраивается в существующую модель данных, а не переписывает её.
> Деньги и счётчики — только `INTEGER` (правило проекта: «Все суммы — `int` (копейки).
> Никогда `float`/`Decimal` для денег»). Новая сущность `clubs` НЕ вводится — расширяем
> таблицу `habits`. `weekly_n` расписание вынесено за рамки этого ТЗ.

---

## 1. Общее описание

В профиле пользователя появляется визуальный «персонаж» с набором **характеристик** —
по одной на каждый клуб, в котором пользователь участвует. Характеристика растёт при
успешном чек-ине и падает при штрафе. Цель — наглядный прогресс, мотивация через
социальное сравнение (лидерборд) и статусную систему.

**Ограничения относительно v1 ТЗ:**
- Не вводим таблицу `clubs`. Все клубные поля добавляются в существующую `habits`.
- `price`, `penalty_amount` остаются `INTEGER` копейках (как сейчас в `habits`).
- `stat_gain_per_checkin` / `stat_loss_per_miss` — `INTEGER` (условные «очки», не рубли).
  Знак «+0.5» из v1 заменяется на «−1 / +2» (целочисленные шаги, см. п. 5).
- Расписание — **только ежедневное** (`schedule_type = 'daily'`). Поддержку
  `weekly_n` оформляем отдельным ТЗ после стабилизации MVP.
- Суммы порогов статусов — `INTEGER`. `Decimal` нигде не используется.

---

## 2. Структура данных

### 2.1. Расширение таблицы `habits` (а не новая `clubs`)

| Поле | Тип | Default | Описание |
|---|---|---|---|
| photo_url | VARCHAR(512) | NULL | Фото клуба для отображения |
| telegram_invite_link | VARCHAR(512) | NULL | Ссылка-инвайт в Telegram-группу |
| stat_name | VARCHAR(64) | **NOT NULL** | Название характеристики («Интеллект», «Эстетика тела») |
| stat_icon | VARCHAR(16) | NULL | Эмодзи/иконка характеристики (1–4 символа) |
| stat_gain_per_checkin | INTEGER | 2 | Прирост за успешный чек-ин |
| stat_loss_per_miss | INTEGER | 1 | Убыль за штраф (списание `> 0`) |
| member_limit | INTEGER | NULL | Лимит участников, NULL = без лимита |
| curator_id | BIGINT (FK → users.id) | NULL | Куратор/создатель клуба |

**Неизменные поля `habits` (наследуются из `docs/06-data-model.md`):**
`id`, `title`, `description`, `chat_id`, `checkin_window_start`, `checkin_window_end`,
`timezone`, `penalty_amount`, `price_month`, `proof_type`, `prize_pool`, `is_active`,
`created_at`.

**Удалено из v1 ТЗ:**
- ~~`schedule_type ENUM(daily, weekly_n)`~~ — MVP остаётся на ежедневных чек-инах.
- ~~`schedule_target INT`~~ — не применимо для daily.
- ~~`checkin_format ENUM(photo, video, text)`~~ — уже есть как `proof_type ENUM(video_note, photo, text)`.
- ~~`billing_period ENUM(week, month)`~~ — текущий биллинг остаётся помесячный; weekly billing вынесен в отдельное ТЗ.
- ~~`price DECIMAL`~~ — `price_month INTEGER` уже существует.

**Миграция `007_character_and_club_fields.sql`:**
```sql
ALTER TABLE habits
    ADD COLUMN photo_url VARCHAR(512),
    ADD COLUMN telegram_invite_link VARCHAR(512),
    ADD COLUMN stat_name VARCHAR(64) NOT NULL DEFAULT 'Дисциплина',
    ADD COLUMN stat_icon VARCHAR(16),
    ADD COLUMN stat_gain_per_checkin INTEGER NOT NULL DEFAULT 2,
    ADD COLUMN stat_loss_per_miss INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN member_limit INTEGER,
    ADD COLUMN curator_id BIGINT REFERENCES users(id);

ALTER TABLE habits
    ADD CONSTRAINT habits_stat_loss_positive CHECK (stat_loss_per_miss > 0),
    ADD CONSTRAINT habits_stat_gain_positive CHECK (stat_gain_per_checkin > 0);

CREATE INDEX ix_habits_curator ON habits(curator_id) WHERE curator_id IS NOT NULL;
```

**Backfill:** для существующих клубов `stat_name = 'Дисциплина'`, `stat_icon = '🔥'`,
`stat_gain_per_checkin = 2`, `stat_loss_per_miss = 1`. Проверить через
`SELECT COUNT(*) FROM habits WHERE stat_name IS NULL;` после миграции — должен быть 0.

### 2.2. Новая таблица `user_stats`

| Поле | Тип | Default | Описание |
|---|---|---|---|
| id | UUID | gen_random_uuid() | PK |
| user_id | BIGINT | NOT NULL, FK → users.id | Пользователь |
| habit_id | UUID | NOT NULL, FK → habits.id | Клуб, к которому привязана характеристика |
| value | BIGINT | 0 | Текущее значение характеристики |
| last_checkin_at | TIMESTAMPTZ | NULL | Дата последнего успешного чек-ина в этом клубе |
| is_frozen | BOOLEAN | false | Заморожена ли характеристика |
| frozen_at | TIMESTAMPTZ | NULL | Когда заморожена |
| frozen_reason_text | VARCHAR(256) | 'Отказался расти дальше' | Текст при заморозке |
| created_at | TIMESTAMPTZ | now() | |
| updated_at | TIMESTAMPTZ | now() | |

**Unique index:** `(user_id, habit_id)` — одна характеристика на клуб у пользователя.

**CHECK constraints:**
- `value >= 0` (никогда не уходит в минус).
- `is_frozen = false OR frozen_at IS NOT NULL` (если заморожено — дата обязательна).
- `is_frozen = true OR frozen_at IS NULL` (если не заморожено — даты быть не должно).

**Индексы:**
- `ix_user_stats_user` на `(user_id)` — для профиля.
- `ix_user_stats_habit_value` на `(habit_id, value DESC)` — для лидерборда по характеристике.
- `ix_user_stats_freeze_cron` на `(is_frozen, last_checkin_at)` WHERE `is_frozen = false`
  — для cron-заморозки.

**Связь с другими таблицами:**
- `user_stats.habit_id` НЕ ссылается на `memberships.id` намеренно — характеристика
  переживает `membership.status = 'left'`. Если пользователь выйдет и вернётся в клуб,
  история восстанавливается (п. 3.4).
- `user_stats.value` — **НЕ** денежная сумма. Это условные «очки дисциплины». В `transactions`
  не пишется. В `bonus_points` (на `users`) не сливается. Это **отдельная** ось прогресса.

### 2.3. Новая таблица `user_statuses` (справочник)

| Поле | Тип | Описание |
|---|---|---|
| id | UUID | PK |
| status_name | VARCHAR(64) | «Новичок», «Практик», «Мастер», «Легенда» |
| min_threshold | INTEGER | Мин. сумма ВСЕХ `user_stats.value` для получения |
| icon_url | VARCHAR(512) | Иконка/бейдж |
| sort_order | INTEGER | Порядок отображения |

**Семя (default data, отдельная миграция `008_user_statuses_seed.sql`):**

| status_name | min_threshold | sort_order | icon_url |
|---|---|---|---|
| Новичок | 0 | 1 | /badges/newbie.svg |
| Практик | 30 | 2 | /badges/practitioner.svg |
| Мастер | 150 | 3 | /badges/master.svg |
| Легенда | 500 | 4 | /badges/legend.svg |

**Вычисление статуса:** `SELECT MAX(min_threshold) FROM user_statuses WHERE min_threshold <= :sum`.
Никогда не суммируется с `users.bonus_points` или сезонными рангами — это **отдельная** лестница.

**CHECK:** `min_threshold >= 0` и `UNIQUE(sort_order)`.

---

## 3. Бизнес-логика

### 3.1. Создание клуба (расширение существующего `POST /api/v1/habits`)

Куратор (`users.id`) указывает: фото, Telegram-инвайт, название, описание,
`stat_name` (обязательно, не пустое), `stat_icon`, **расписание только daily**,
окно чек-ина, формат подтверждения (`proof_type`), `price_month`, `penalty_amount`,
при необходимости — `stat_gain_per_checkin` / `stat_loss_per_miss` (по умолчанию 2 / 1),
лимит участников.

**Валидация (в `services/habit_service.py`, не в роуте):**
- `telegram_invite_link` либо NULL, либо начинается с `https://t.me/` или `https://telegram.me/`.
- `stat_name` — не пустая строка после `strip()`, длина ≤ 64.
- `price_month > 0`, `penalty_amount > 0` — `INTEGER` (правило проекта: деньги = int).
- `stat_gain_per_checkin > 0`, `stat_loss_per_miss > 0`.
- `member_limit` либо NULL, либо `> 0`.

### 3.2. Начисление характеристики при успешном чек-ине

**Точка вызова:** внутри `CheckinService.process_checkin()` (в той же транзакции,
что и запись в `checkins`). После успешного `INSERT INTO checkins` — увеличиваем
`user_stats.value`. Один DB round-trip + транзакция.

**Действия:**
1. `SELECT ... FOR UPDATE` на строку `user_stats(user_id, habit_id)` (или `INSERT` если
   не существует — функция `get_or_create_for_update` в репозитории).
2. Если `is_frozen = true`: разморозить (`is_frozen = false`, `frozen_at = NULL`).
3. `value = value + habit.stat_gain_per_checkin`.
4. `last_checkin_at = NOW()`.
5. `updated_at = NOW()`.

**Идемпотентность:** если чек-ин за этот день уже существует
(`uq_checkins_membership_date`), `process_checkin` возвращает `created=False` и
**не вызывает** инкремент `user_stats`. Повторное сообщение в чат не даёт двойной
характеристики — это та же защита, что от двойного штрафа.

### 3.3. Списание характеристики при штрафе

**Точка вызова:** внутри `PenaltyService.process_penalty()` и в `close_catch_window`
worker (для `reason = 'window_closed_no_catch'`). В одной транзакции с штрафом.

**Действия:**
1. `SELECT ... FOR UPDATE` на `user_stats(user_id, habit_id)`.
2. `value = GREATEST(0, value - habit.stat_loss_per_miss)` — никогда не уходит в минус.
3. `updated_at = NOW()`.

**При `reason = 'caught'`:** списание характеристики происходит **независимо** от
`catcher_membership_id`. Ловить другого — это его буст, не твой щит.

**При `suspicious_pairs`:** даже если `catcher_bonus_points` не начислен,
`user_stats.value` нарушителя всё равно уменьшается. Дисциплина не ослабляется
(это согласуется с п. 4.5 `docs/06-data-model.md`: «штраф списывается как обычно»).

### 3.4. Заморозка характеристики при неактивности

**Условие (worker `freeze_inactive_stats`):** ежедневная задача проверяет все
`user_stats WHERE is_frozen = false AND last_checkin_at < NOW() - INTERVAL '30 days'`.

**Действия:**
1. `is_frozen = true`.
2. `frozen_at = NOW()`.
3. `frozen_reason_text = 'Отказался расти дальше'` (по умолчанию; в будущем — поле в `habits`).
4. `value` сохраняется, не сбрасывается.

**Расписание:** cron раз в сутки (например, 04:00 UTC — до начала активных окон).
Привязка к одному общему времени допустима (это метрика дисциплины, не клубное окно),
но `last_checkin_at` всё равно хранится в UTC.

**Возврат из заморозки:** любой успешный чек-ин автоматически размораживает
(см. п. 3.2 шаг 2) — `value` продолжает расти с сохранённого уровня.

**Важно: отличие от `membership.status = 'paused'`.**

| Состояние | Причина | Эффект | Снимается |
|---|---|---|---|
| `membership.status = 'paused'` | Депозит = 0 | Чек-ины не принимаются, штрафы не списываются, в лидербордах помечается | Пополнением депозита |
| `user_stats.is_frozen = true` | 30 дней без чек-ина | Чек-ины продолжают работать, характеристика просто не растёт и не падает | Успешным чек-ином в клубе |

**Это два независимых механизма.** Клуб может быть `paused`, а характеристика —
`active` (если до паузы депозита был свежий чек-ин). И наоборот.

**При выходе из клуба (`membership.status = 'left'`):** строка `user_stats`
НЕ удаляется. При повторном `POST /habits/{id}/join` проверка существования
`user_stats(user_id, habit_id)` возвращает существующую запись — `value`
восстанавливается как есть.

### 3.5. Отображение персонажа в профиле

**Backend эндпоинт `GET /api/v1/character/me`:**

```json
{
  "total_value": 142,
  "status": {
    "name": "Практик",
    "icon_url": "/badges/practitioner.svg",
    "next_threshold": 150,
    "next_status": "Мастер"
  },
  "stats": [
    {
      "habit_id": "uuid",
      "habit_title": "Планка 30 мин",
      "stat_name": "Эстетика тела",
      "stat_icon": "💪",
      "value": 58,
      "is_frozen": false,
      "frozen_reason_text": null,
      "last_checkin_at": "2026-07-21T05:14:00Z"
    },
    {
      "habit_id": "uuid",
      "habit_title": "Чтение",
      "stat_name": "Интеллект",
      "stat_icon": "🧠",
      "value": 24,
      "is_frozen": true,
      "frozen_at": "2026-06-12T10:00:00Z",
      "frozen_reason_text": "Отказался расти дальше",
      "last_checkin_at": "2026-05-12T08:30:00Z"
    }
  ]
}
```

**Замороженная характеристика визуально отличается** (UI-требование): приглушённый
цвет, иконка ❄️, текст `frozen_reason_text` под значением, дата `frozen_at`.

### 3.6. Админский флоу создания клубов (Owner only)

Владелец платформы создаёт клубы через **отдельный бот и отдельный Mini App** —
не через основной пользовательский флоу. Это нужно для разделения прав и аудита.

### 3.6.1. Архитектура

- **Основной бот** `@PrideClubBot` (уже работает) — пользовательский флоу:
  `/start`, чек-ины, штрафы, пополнение. В нём владелец действует как обычный
  пользователь (у него тоже могут быть `memberships`, депозит).
- **Админ-бот** `@PrideClubAdminBot` (создаём через BotFather, токен хранится в
  `.env` как `BOT_TOKEN_ADMIN`) — открывает отдельный Mini App `https://admin.prideclub.fun`
  со своим UI: список клубов, создание, тумблер активности, редактирование, архивация.
- Админский Mini App → бэкенд по тому же API, но в отдельном контуре **`/admin/v1/*`**
  (по аналогии с уже существующими `/api/v1/*` и `/internal/*`).
- Авторизация: `OWNER_TELEGRAM_ID` (захардкожен в `.env` на сервере, `chmod 600`).
  Когда понадобится дать доступ ещё кому-то — переедем на таблицу `admins(user_id, role)`,
  сейчас один владелец — проще.

### 3.6.2. Контур авторизации `/admin/v1/*`

Отдельная middleware-цепочка:
1. Проверка `X-Telegram-Init-Data` (как для `/api/v1/*`).
2. Сравнение `telegram_user.id == settings.OWNER_TELEGRAM_ID`.
3. Если не совпадает → `403 admin_only`.

`user_id` берётся ТОЛЬКО из `request.state.telegram_user` (как везде) — никаких
параметров `user_id` в теле/querystring.

Все админ-действия логируются с `extra={"admin_id": ..., "action": ..., "target": ...}`
и `duration_ms`. Без PII (только `user_id`).

### 3.6.3. Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/admin/v1/habits` | Создать клуб (с `is_active = false` по умолчанию) |
| `GET` | `/admin/v1/habits` | Список клубов (все, включая архивированные) |
| `GET` | `/admin/v1/habits/{id}` | Детали клуба |
| `PATCH` | `/admin/v1/habits/{id}` | Редактировать поля (включая `telegram_invite_link` если протухла) |
| `POST` | `/admin/v1/habits/{id}/activate` | Тумблер `is_active` (true/false) |
| `POST` | `/admin/v1/habits/{id}/archive` | Soft-delete: `is_active = false`, `archived_at = now()` |
| `POST` | `/admin/v1/habits/{id}/restore` | `archived_at = null` (восстановление) |

**Что нельзя админу (out of scope):**
- ❌ Удалять чекин-историю или штрафы.
- ❌ Менять `prize_pool` вручную (только через штрафы/выплаты).
- ❌ Создавать чат в Telegram через Bot API (куратор/владелец даёт готовый
  `telegram_invite_link`, см. п. 3.6.5).
- ❌ Редактировать финансовые поля (`penalty_amount`, `price_month`) после того как
  в клуб кто-то вступил — иначе сломается аудит. Поля можно править только если
  `COUNT(memberships WHERE habit_id = id AND status != 'left') = 0`. После первого
  вступления — заморожены (см. п. 3.6.7).

### 3.6.4. Создание клуба: правила

`POST /admin/v1/habits` создаёт клуб **всегда с `is_active = false`**. Это даёт
владельцу время всё проверить перед публикацией.

**Тело запроса:**
```json
{
  "title": "Планка 30 мин",
  "description": "Держим планку 30 минут каждый день",
  "photo_url": "https://...",
  "telegram_invite_link": "https://t.me/+abcdef",
  "stat_name": "Эстетика тела",
  "stat_icon": "💪",
  "checkin_window_start": "06:00",
  "checkin_window_end": "23:59",
  "timezone": "Europe/Moscow",
  "proof_type": "video_note",
  "price_month": 100000,
  "penalty_amount": 10000,
  "stat_gain_per_checkin": 2,
  "stat_loss_per_miss": 1,
  "member_limit": null
}
```

**Валидация (в `services/habit_service.py`, не в роутах):**
- `title` — непустой, длина 3–128.
- `stat_name` — непустой после `strip()`, длина ≤ 64.
- `telegram_invite_link` — NULL или начинается с `https://t.me/+`, `https://t.me/`,
  `https://telegram.me/`. Проверка формата (regex), **не HTTP-запрос** (это утечка
  чужой группы).
- `checkin_window_start < checkin_window_end` (для daily это просто).
- `timezone` — валидный IANA TZ (через `zoneinfo.ZoneInfo(...)`).
- `proof_type` ∈ `video_note | photo | text`.
- `price_month > 0`, `penalty_amount > 0` — `INTEGER` копейки (правило проекта).
- `stat_gain_per_checkin > 0`, `stat_loss_per_miss > 0`.
- `member_limit` — NULL или `> 0`.
- `is_active` принимается, но в `POST` всегда игнорируется и принудительно
  ставится `false`. Для активации — отдельный `POST /activate`.

**Все ошибки — через `HabitValidationError` (DomainError, status_code=400,
code=`habit_validation`).**

### 3.6.5. Telegram-инвайт: кто создаёт чат

**Владелец создаёт группу в Telegram вручную** (или это уже существующая группа) и
передаёт инвайт-ссылку через форму создания. Бот **не создаёт** чаты через
`createChat`/`createChatInviteLink` — это:
- усложняет код (нужно хранить bot ownership чата, обрабатывать privacy exceptions),
- не нужно для MVP (один владелец, одна группа на старте),
- создаёт лишнюю точку отказа (если бот потеряет права админа — инвайт сломается).

**`telegram_invite_link` — отдельное редактируемое поле** (как просил владелец).
Если группа пересоздана — админ делает `PATCH /habits/{id}` с новой ссылкой. У
существующих участников ссылка из `habit.telegram_invite_link` отображается в
Mini App как «Перейти в чат клуба».

### 3.6.6. Гейт `is_active` на стороне пользователя

Клуб виден пользователям **только если `is_active = true AND archived_at IS NULL`**.

Все публичные запросы фильтруют:
- `GET /marketplace` — `WHERE is_active = true AND archived_at IS NULL`.
- `GET /habits/{id}/today` — если клуб неактивен или в архиве → `404 habit_not_found`.
- `POST /habits/{id}/join` — если неактивен → `409 habit_inactive`. Если в архиве →
  `404 habit_not_found` (архивный клуб нельзя «вступить заново», только
  восстановить через админку).

### 3.6.7. Заморозка финансовых полей после первого вступления

`price_month`, `penalty_amount` НЕ редактируются через PATCH, если в клубе уже
есть хотя бы одно `memberships WHERE habit_id = :id AND status != 'left'`.
Админский UI скрывает эти поля после вступления первого участника.

Причина: иначе сломался бы аудит финансовой истории. Если нужно реально изменить
цену/штраф — заводим новый клуб и мигрируем участников отдельным ТЗ.

Остальные поля (`title`, `description`, `photo_url`, `telegram_invite_link`,
`stat_*`, окно чек-ина) — редактируются всегда.

### 3.6.8. Soft-delete (`archive`)

`POST /admin/v1/habits/{id}/archive`:
1. `is_active = false`.
2. `archived_at = now()`.
3. Все активные `memberships` остаются как есть (status не меняется).
4. Клуб исчезает из `/marketplace`, но чекин-история, штрафы и балансы
   участников сохраняются в БД.
5. Участники при попытке `GET /habits/{id}/today` получают `404 habit_not_found`.
6. Возвращается `200 {"ok": true, "archived_at": "..."}`.

Восстановление (`POST /admin/v1/habits/{id}/restore`):
1. `archived_at = null`.
2. `is_active` остаётся `false` — админ явно активирует через `/activate`.

Hard delete (`DELETE FROM habits WHERE id = ...`) — **запрещён** на уровне сервиса.
`Habit` не имеет метода `delete()` в репозитории. Это защищает
`transactions → penalties → checkins` FK-цепочку.

### 3.6.9. Миграция `007b_habit_admin_fields.sql`

```sql
ALTER TABLE habits
    ADD COLUMN archived_at TIMESTAMPTZ;

CREATE INDEX ix_habits_active
    ON habits(is_active, archived_at)
    WHERE is_active = true AND archived_at IS NULL;
```

`ix_habits_active` — частичный индекс, обслуживает горячий путь `/marketplace`.

### 3.6.10. ENV-переменные

```bash
# .env на сервере, chmod 600
OWNER_TELEGRAM_ID=123456789          # ID владельца платформы
BOT_TOKEN_ADMIN=...                  # токен @PrideClubAdminBot
ADMIN_WEBAPP_URL=https://admin.prideclub.fun
ADMIN_MINI_APP_SHORT_NAME=admin      # Short name в BotFather для админского Mini App
```

Токены **никогда** не попадают в репозиторий, логи или документацию. Если случайно
попали в чат — немедленно отзывать через BotFather `/revoke` (см. `docs/07-security-and-ops.md:270-278`).

---

## 3.7. Лидерборд внутри клуба по характеристике

**Backend эндпоинт `GET /api/v1/leaderboard/stat?habit_id={uuid}`:**

```json
{
  "habit_id": "uuid",
  "stat_name": "Эстетика тела",
  "metric_label": "Очки характеристики",
  "members": [
    {
      "rank": 1,
      "user_id": 123,
      "first_name_initial": "Д",  // только инициал, не полное имя (ФЗ-152)
      "value": 87,
      "total_value": 142,           // суммарно по всем клубам — для общего статуса
      "status_name": "Практик",
      "is_frozen": false
    }
  ]
}
```

**Сортировка:** `ORDER BY value DESC, user_id ASC` (стабильная при равенстве).
Исключаются `membership.status = 'left'`. `is_frozen = true` — остаются в рейтинге
с пометкой «❄».

**Антифрод:** НЕ исключаем из лидерборда членов с подозрительными парами (в отличие от
catch-бонусов) — характеристика своя, не зависит от «улова».

**Новый таб в существующем `LeaderboardPage`:** «📊 Характеристика» рядом с
«🔥 Серии / 🎯 Ловцы / 💀 Позор». Таб показывается только если в текущем клубе
есть хотя бы 1 `user_stats` с `value > 0` (иначе empty state).

---

## 4. UI-требования

1. **Экран «Мой персонаж»** (вкладка внутри `ProfilePage` или отдельный маршрут
   `/character`): карточка со статусом (иконка + название + прогресс до следующего),
   общая сумма `total_value`, список характеристик карточками (иконка, название, число,
   статус заморозки).
2. **Таб «📊 Характеристика» в `LeaderboardPage`** (внутри клуба): список участников
   с `value`, инициалами и общим статусом.
3. **Замороженная характеристика**: приглушённый фон, иконка ❄️, текст-причина, дата.
4. **Level-up анимация**: при пересечении порога нового статуса — toast
   «Новый статус: {name}» + haptic `impact('medium')`. Триггерится на клиенте
   сравнением `total_value` до и после запроса.
5. **Прогресс-бар до следующего статуса**: горизонтальная полоса (фиолет → бирюза,
   из `docs/05-ui-ux.md:144`).
6. **Никаких новых цветов вне палитры** из `docs/05-ui-ux.md` — золото для топ-3,
   бирюза для активных, коралл для замороженных (по аналогии со штрафами).

---

## 5. Конфигурация по умолчанию (выносим в `core/constants.py`)

```python
class CharacterConfig:
    """Конфиг механики 'Персонаж и характеристики'."""

    # Прирост/убыль по умолчанию (если в habits не переопределено).
    DEFAULT_STAT_GAIN_PER_CHECKIN = 2
    DEFAULT_STAT_LOSS_PER_MISS = 1

    # Заморозка: дней без чек-ина.
    FREEZE_AFTER_DAYS_INACTIVE = 30

    # Текст причины заморозки по умолчанию.
    DEFAULT_FROZEN_REASON = "Отказался расти дальше"

    # Минимальное total_value для отображения персонажа (если 0 — скрываем блок).
    MIN_TOTAL_VALUE_TO_SHOW = 1

    # Крон: час UTC запуска freeze_inactive_stats.
    FREEZE_CRON_HOUR_UTC = 4
```

**Никаких магических чисел в сервисах/роутах — только через `CharacterConfig`.**

---

## 6. Реализация (декомпозиция по слоям)

### 6.1. Backend

| Слой | Новые файлы | Изменяемые |
|---|---|---|
| `models/` | `user_stats.py`, `user_status.py` | `habit.py` (новые поля), `__init__.py` (реэкспорт) |
| `repositories/` | `user_stats_repository.py` (с `lock_for_update`, `get_or_create_for_update`) | — |
| `services/` | `character_service.py` (`get_character`, `increment_on_checkin`, `decrement_on_penalty`, `apply_freeze`, `get_leaderboard`) | `checkin_service.py` (вызов increment), `penalty_service.py` (вызов decrement) |
| `api/v1/` | `character.py` (`GET /character/me`), расширение `leaderboard.py` (`GET /leaderboard/stat?habit_id=`) | — |
| `api/admin/v1/` | `habits.py` (`POST/GET/PATCH`, `/activate`, `/archive`, `/restore`), `__init__.py` (новый blueprint), `middleware.py` (owner check) | — |
| `core/` | дополнение `config.py` (`OWNER_TELEGRAM_ID`, `ADMIN_BOT_TOKEN`) | `constants.py` (`CharacterConfig`) |
| `core/` | дополнение `constants.py` (`CharacterConfig`) | — |
| `tasks/` (worker) | `freeze_inactive_stats.py` | `beat_schedule.py` (ежедневно в `FREEZE_CRON_HOUR_UTC`) |
| `alembic/versions/` | `007_character_and_club_fields.py`, `008_user_statuses_seed.py` | — |

**Инварианты реализации:**
- `character_service` инжектит `UserStatsRepository` через конструктор (DI), не создаёт
  сессию внутри.
- `increment_on_checkin` / `decrement_on_penalty` вызываются из существующих сервисов
  **в той же транзакции** (не открывают свою). Это значит: если `process_checkin`
  откатится — откатится и инкремент `user_stats`. Это согласуется с правилом
  проекта «одна транзакция = один handler».
- `freeze_inactive_stats` worker использует bulk update с `LIMIT 1000` за один проход,
  идемпотентный (повторный запуск не дублирует заморозку — `is_frozen` уже `true`).

### 6.2. Frontend (после подключения к API)

| Слой | Новые файлы |
|---|---|
| `shared/api/` | `characterApi.getMe()`, `characterApi.leaderboard(habitId)` |
| `shared/hooks/` | `useCharacter()`, `useCharacterLeaderboard(habitId)` |
| `pages/` | `Character/CharacterPage.tsx` (или вкладка в Profile) |
| `shared/ui/` | `StatCard`, `StatusBadge` (расширение существующего), `LevelUpToast` |

**Подключение к API — отдельная задача (см. `docs/09-prod-readiness.md:127-137`)**.
Это ТЗ описывает только backend + UI-требования. После реализации backend — фича
добавляется в фронт в рамках задачи «Подключение frontend к API».

---

## 7. Тест-план (Definition of Done)

### Unit (pytest)
- [ ] `test_increment_on_checkin_creates_new_stat_for_first_time` — `user_stats` создаётся при первом чек-ине.
- [ ] `test_increment_on_checkin_unfreezes_frozen_stat` — `is_frozen` → `false`, `value` растёт.
- [ ] `test_increment_on_checkin_duplicate_no_double_increment` — повторный чек-ин за день не даёт `+2`.
- [ ] `test_decrement_on_penalty_floors_at_zero` — `value` не уходит в минус.
- [ ] `test_freeze_after_30_days_inactive` — worker переводит в `is_frozen=true`.
- [ ] `test_status_calculation_picks_highest_threshold` — сумма 100 → «Практик» (30), не «Новичок».
- [ ] `test_leave_club_does_not_delete_stats` — `membership.status='left'` не удаляет `user_stats`.

### Integration
- [ ] E2E: успешный чек-ин → `user_stats.value` инкрементирован в той же транзакции.
- [ ] E2E: штраф по `caught` → `user_stats.value` декрементирован.
- [ ] E2E: штраф по `window_closed_no_catch` → тоже декрементирует.
- [ ] `make migrate-test` (upgrade head → downgrade base → upgrade head) проходит.

### Anti-fraud / edge cases
- [ ] `catcher_bonus_points` НЕ начисляется при `suspicious_pairs` — но `user_stats.value` нарушителя всё равно падает (две независимые механики).
- [ ] Cron `freeze_inactive_stats` идемпотентен (повторный запуск через час — 0 изменений).
- [ ] `user_stats.value` не пишется в `transactions` (отдельная ось, не деньги).

---

## 8. Открытые вопросы для дальнейшего уточнения

Решены в этой версии:
1. ✅ Деньги — `INTEGER` (без `Decimal`).
2. ✅ `clubs` не вводится, расширяем `habits`.
3. ✅ `weekly_n` — отдельное ТЗ позже.
4. ✅ Пауза членства и заморозка характеристики — две независимые механики.
5. ✅ Лидерборд по характеристике — отдельный таб, не заменяет streak/catches/shame.

Остаются на будущее:
1. **Полное удаление `user_stats`** при `is_frozen=true` дольше N месяцев (например, 6)?
   Сейчас: **никогда не удаляем**, чтобы при повторном вступлении история восстановилась.
2. **Статусы — per-club или глобальные?** Сейчас: **глобальные** по сумме всех характеристик
   (`SUM(user_stats.value)`). Это согласуется с v1 ТЗ, но может потребовать
   пересмотра, если игроки будут «фармить» лёгкие клубы.
3. **Приватность лидерборда характеристики** — возможность скрыть своё значение?
   Сейчас: **нет**, все участники клуба видят всех (как в streak/catches).

---

## 9. Что НЕ делается в этой итерации (явно вне scope)

- ❌ Поддержка `weekly_n` расписания — отдельное ТЗ.
- ❌ Платный weekly billing — отдельное ТЗ.
- ❌ Удаление `user_stats` через 6+ месяцев заморозки.
- ❌ Приватные лидерборды.
- ❌ AI-комендант (v2, см. `docs/01-concept.md:124`).
- ❌ Кастомные `frozen_reason_text` per-club (сейчас один дефолт для всех).
- ❌ Кураторы как отдельная роль (есть только `owner` через `OWNER_TELEGRAM_ID`).
- ❌ Создание Telegram-чатов через Bot API — инвайт передаёт владелец.
- ❌ Редактирование финансовых полей после первого вступления (заморожены).
- ❌ Hard delete клубов (только soft archive).
- ❌ Frontend-реализация (см. `docs/09-prod-readiness.md:127-137` — сначала подключение всего фронта к API).

---

## 10. Порядок реализации (после подключения фронта к API)

> **Статус актуален на 21.07.2026 22:30 CEST.** Реальное состояние vs план.

### Фаза A — Админский флоу клубов ✅ **DONE на проде**

| # | Шаг | Статус | Комментарий |
|---|---|---|---|
| 1 | Миграция `007b_habit_admin_fields.sql` | ✅ | Файл: `apps/backend/alembic/versions/007_habit_admin_fields.py`. `archived_at TIMESTAMPTZ NULL` + partial idx `ix_habits_active`. Round-trip ✅. |
| 2 | Admin auth middleware | ✅ | `/admin/v1/*` ветка в `app/core/middleware.py`: `owner_telegram_id` (503 если 0) → initData (401) → owner-check (403) → `request.state.telegram_user`. 4 теста. |
| 3 | `HabitService.create/update/archive/restore/activate` | ✅ | `app/services/habit_service.py` (~330 строк). Валидация title/stat_name/telegram_invite_link (regex)/timezone (zoneinfo)/prices > 0/gain > 0/member_limit. Заморозка `price_month`/`penalty_amount` через `count_active_members > 0`. **34 unit-теста**. |
| 4 | Admin API endpoints | ✅ | `app/api/admin/{__init__.py,v1/{__init__.py,habits.py}}` + 6 Pydantic schemas. 7 эндпоинтов. **13 интеграционных тестов**. |
| 5 | Гейт в публичных роутах | ✅ | `memberships.py::join` (404/409), `members.py::list_members` (404), `checkin_service.py::get_today_status` (404). `HabitRepository.list_active` обновлён до `WHERE is_active=true AND archived_at IS NULL`. **4 gate-теста**. |
| 6 | Деплой | ✅ | Накатил на прод `169.58.52.78`. `OWNER_TELEGRAM_ID=7295309649` добавлен в `/app/infra/.env` (chmod 600) и в `infra/docker-compose.yml` (`x-backend-env`). `pg_dump` в `/app/backups/pre_phaseA_20260721.sql.gz`. Smoke curl через `jq`: 7 admin + 3 public gate проверок — все ✅. |

**Результат Фазы A:** 110 backend тестов passed, прод-сервер имеет работающий admin-контур. Проверено: create → activate → patch → archive → restore → activate-archived-404 → marketplace-скрывает-неактивный → join-409 / 404 → members-404.

**Что НЕ сделано в Фазе A (out of MVP):**
- UI админского Mini App (`admin.prideclub.fun`). Управление через curl/Postman.
- Регистрация `@PrideClubAdminBot` в BotFather. Сейчас используется основной `BOT_TOKEN` для initData.
- nginx для `admin.prideclub.fun` — не настроен (нет смысла без Mini App).

### Фаза B — Персонаж и характеристики 🔲 **TODO**

| # | Шаг | Статус | Файлы |
|---|---|---|---|
| 1 | Backend каркас | 🔲 | Миграция `008_character_and_club_fields.py` уже накатана на прод (часть Фазы A) — поля `habits.stat_*`, `photo_url`, `telegram_invite_link`, `member_limit`, `curator_id` готовы. Нужны: `models/user_stats.py`, `models/user_status.py`, `repositories/user_stats_repository.py`, `core/constants.py:CharacterConfig`. |
| 2 | Backend инкремент/декремент | 🔲 | `services/character_service.py` (`increment_on_checkin`, `decrement_on_penalty`) + хуки в `CheckinService.process_checkin` и `PenaltyService.apply_catch` / `close_catch_window`. `SELECT FOR UPDATE` через `lock_for_update`. |
| 3 | Backend API + статус + лидерборд | 🔲 | `api/v1/character.py` (`GET /character/me`), расширение `api/v1/leaderboard.py` (`GET /leaderboard/stat?habit_id=`), seed-миграция `009_user_statuses_seed.py`. |
| 4 | Worker заморозки | 🔲 | `apps/worker/worker/tasks/freeze_inactive_stats.py` + `beat_schedule.py` cron в `FREEZE_CRON_HOUR_UTC=4`. |
| 5 | Документация | 🔲 | `docs/06-data-model.md` (новые таблицы), `docs/01-concept.md` (геймификация). |
| 6 | Frontend | ⏸ | Отложено до подключения основного фронта к API (`docs/09-prod-readiness.md:127-137`). |

### Итого

- Фаза A: **DONE на backend + проде**. UI админки — отдельно.
- Фаза B: **TODO**, ~5-6 дней.
- Frontend всё ещё блокер для полного end-to-end (API готовы, фронт не подключён).

---

## 11. Связь с уже принятыми решениями (ссылки на `docs/`)

| Решение в этом ТЗ | Источник в `docs/` |
|---|---|
| Деньги = `INTEGER` | `AGENTS.md` правило #8; `docs/04-code-standards.md:392` |
| TZ клуба, не пользователя | `docs/06-data-model.md:39-40` |
| `user_id` только из `request.state.telegram_user` | `docs/07-security-and-ops.md:193` |
| Не логировать PII (только `user_id`) | `docs/07-security-and-ops.md:281-283` |
| Миграции append-only, `make migrate-test` | `docs/04-code-standards.md:10`; AGENTS.md |
| `FOR UPDATE` при изменении денег/счётчиков | `docs/06-data-model.md:381-398` |
| Доменные исключения + глобальный обработчик | `docs/04-code-standards.md:298-326` |
| Сезонный снапшот правил | `docs/06-data-model.md:498-516` |
| `suspicious_pairs` не ослабляет штрафы | `docs/06-data-model.md:143-145` |

Если в процессе реализации потребуется отклониться от любого из этих решений —
**остановиться и спросить**, а не править молча.

---

## 12. Changelog

- **v2.1 (21.07.2026 22:30 CEST)** — актуализация статуса после деплоя Фазы A на прод:
  - Раздел 10 переписан в табличный формат с реальным статусом ✅/🔲.
  - Фаза A полностью завершена на backend + проде: миграции накатаны (`006_suspicious_pairs_index`, `007_habit_admin_fields`, `008_character_and_club_fields`), owner-auth работает (smoke curl через `jq` подтвердил все 7 admin-эндпоинтов + 3 public gate), `OWNER_TELEGRAM_ID=7295309649` сконфигурирован. UI админки отложен.
  - Фаза B остаётся TODO. Поля `habits.stat_*` уже на проде (приехали с миграцией 008 в рамках Фазы A), что упрощает Фазу B шаг 1.
- **v2.0 (21.07.2026)** — фикс по итогам ревью: деньги = `INTEGER`, без `clubs`-таблицы, без `weekly_n`, переименовано `ADMIN_BOT_TOKEN`→`BOT_TOKEN_ADMIN`, разделы 6.1/9/10 переработаны под Фазу A + Фазу B.
