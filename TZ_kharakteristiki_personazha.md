# Техническое задание
## Модуль «Персонаж и характеристики» (геймификация привычек)

Версия: 3.0 — актуализация после инвентаризации кода 22.07.2026
Дата: 22.07.2026 14:20 CEST
Базируется на: `docs/01-concept.md`, `docs/06-data-model.md`, `docs/04-code-standards.md`, `AGENTS.md`

> **Принцип:** фича встраивается в существующую модель данных, а не переписывает её.
> Деньги и счётчики — только `INTEGER` (правило проекта: «Все суммы — `int` (копейки).
> Никогда `float`/`Decimal` для денег»). Новая сущность `clubs` НЕ вводится — расширяем
> таблицу `habits`. `weekly_n` расписание вынесено за рамки этого ТЗ.

---

## 0. Статус реализации (что сделано, что нет)

Версия 2.5 этого ТЗ была написана ДО того, как часть Фазы B реализовали. Версия 3.0
синхронизирована с кодом на `main` (HEAD = `64f231c`) и продом `169.58.52.78`.

| Блок ТЗ | Реализация в коде | Где |
|---|---|---|
| §2.1 Расширение `habits` (8 полей + CHECK'и) | ✅ сделано | `apps/backend/alembic/versions/008_character_and_club_fields.py`, `apps/backend/app/models/habit.py:42-60` |
| §3.6.9 Поле `archived_at` + индекс `ix_habits_active` | ✅ сделано | `apps/backend/alembic/versions/007_habit_admin_fields.py` |
| §3.6.3 Эндпоинты `/admin/v1/habits` (CRUD + activate/archive/restore) | ✅ сделано | `apps/backend/app/api/admin/v1/habits.py` |
| §3.6.2 Owner-gate в middleware | ✅ сделано (в общем `AuthMiddleware`) | `apps/backend/app/core/middleware.py:78-110` |
| §3.6.4 Создание клуба с `is_active=false` | ✅ сделано | `apps/backend/app/services/habit_service.py:52-129` |
| §3.6.6 Фильтр `is_active AND archived_at IS NULL` в публичных запросах | ✅ сделано (repository) | `apps/backend/app/repositories/habit_repository.py:30-58, 67-76, 78-90` |
| §3.6.7 Заморозка финансовых полей после 1-го вступления | ✅ сделано | `apps/backend/app/services/habit_service.py:182-195` |
| §3.6.8 Soft-delete (archive) + восстановление + запрет activate архивного | ✅ сделано | `apps/backend/app/services/habit_service.py:208-265` |
| Hard delete запрещён | ✅ сделано (нет `delete()` в HabitRepository) | `apps/backend/app/repositories/habit_repository.py` |
| §3.6.4 Валидация `telegram_invite_link`, `timezone`, полей | ✅ сделано | `apps/backend/app/services/habit_service.py:268-388` |
| §3.6.10 ENV `OWNER_TELEGRAM_ID`, `BOT_TOKEN_ADMIN` | ✅ сделано | `apps/backend/app/core/config.py:36-37`, `infra/docker-compose.yml:18-19` |
| Admin Mini App UI на `admin.prideclub.fun` | ✅ сделан и задеплоен | `apps/frontend/src/admin/`, `apps/frontend/admin.html` (commit `ad0267b`) |
| §2.2 Новая таблица `user_stats` | ❌ не сделано | — |
| §2.3 Новая таблица `user_statuses` + seed | ❌ не сделано | — |
| §3.2 Инкремент характеристики в `CheckinService.process_checkin` | ❌ не сделано | — |
| §3.3 Декремент характеристики в `PenaltyService.apply_*` | ❌ не сделано | — |
| §3.4 Worker `freeze_inactive_stats` + Celery beat | ❌ не сделано | — |
| §3.5 Эндпоинт `GET /api/v1/character/me` | ❌ не сделано | — |
| §3.7 Эндпоинт `GET /api/v1/leaderboard/stat` | ❌ не сделано | — |
| §5 `CharacterConfig` в `core/constants.py` | ❌ не сделано | — |
| Frontend: CharacterPage, useCharacter, LevelUpToast | ❌ не сделано | — |

**Итого готово:** §3.6 «Админский флоу создания клубов» — полностью, на проде.
**Осталось (собственно Фаза B):** только блоки §2.2, §2.3, §3.2–3.5, §3.7, §5 и frontend.

---

## 1. Общее описание

В профиле пользователя появляется визуальный «персонаж» с набором **характеристик** —
по одной на каждый клуб, в котором пользователь участвует. Характеристика растёт при
успешном чек-ине и падает при штрафе. Цель — наглядный прогресс, мотивация через
социальное сравнение (лидерборд) и статусную систему.

**Ограничения (всё ещё актуальны):**
- Не вводим таблицу `clubs`. Все клубные поля добавляются в существующую `habits` —
  **уже сделано**.
- `price`, `penalty_amount` остаются `INTEGER` копейках.
- `stat_gain_per_checkin` / `stat_loss_per_miss` — `INTEGER` (условные «очки», не рубли).
- Расписание — **только ежедневное**. `weekly_n` оформляем отдельным ТЗ.
- Суммы порогов статусов — `INTEGER`. `Decimal` нигде не используется.

---

## 2. Структура данных

### 2.1. Расширение таблицы `habits` — ✅ УЖЕ СДЕЛАНО

**Миграция:** `apps/backend/alembic/versions/008_character_and_club_fields.py` (revises `007_habit_admin_fields`).

| Поле | Тип | Default | Реализация |
|---|---|---|---|
| `photo_url` | VARCHAR(512) | NULL | `Habit.photo_url` (`models/habit.py:42`) |
| `telegram_invite_link` | VARCHAR(512) | NULL | `Habit.telegram_invite_link` (`models/habit.py:43`) |
| `stat_name` | VARCHAR(64) | `'Дисциплина'` NOT NULL | `Habit.stat_name` (`models/habit.py:45-47`) |
| `stat_icon` | VARCHAR(16) | NULL | `Habit.stat_icon` (`models/habit.py:48`) |
| `stat_gain_per_checkin` | INTEGER | `2` NOT NULL | `Habit.stat_gain_per_checkin` (`models/habit.py:49-51`) |
| `stat_loss_per_miss` | INTEGER | `1` NOT NULL | `Habit.stat_loss_per_miss` (`models/habit.py:52-54`) |
| `member_limit` | INTEGER | NULL | `Habit.member_limit` (`models/habit.py:55`) |
| `curator_id` | BIGINT, FK → `users.id` ON DELETE SET NULL | NULL | `Habit.curator_id` (`models/habit.py:56`) |
| `archived_at` | TIMESTAMPTZ | NULL | `Habit.archived_at` (`models/habit.py:58-60`) |

**CHECK constraints (есть в миграции 008):**
- `habits_stat_loss_positive`: `stat_loss_per_miss > 0`
- `habits_stat_gain_positive`: `stat_gain_per_checkin > 0`
- `habits_member_limit_positive`: `member_limit IS NULL OR member_limit > 0`

**FK:** `fk_habits_curator_id_users (curator_id → users.id) ON DELETE SET NULL`.

**Индексы:**
- `ix_habits_curator ON habits(curator_id) WHERE curator_id IS NOT NULL` (частичный).
- `ix_habits_active ON habits(is_active) WHERE is_active = true AND archived_at IS NULL` (из миграции 007, частичный).

**Backfill на проде (уже произошёл, `server_default` на ALTER):**
- `stat_name='Дисциплина'`, `stat_gain_per_checkin=2`, `stat_loss_per_miss=1` —
  применены одной командой `ALTER ... NOT NULL DEFAULT ...`.

**Проверка после деплоя:** `SELECT COUNT(*) FROM habits WHERE stat_name IS NULL;` → `0`.

### 2.2. Новая таблица `user_stats` — ❌ НЕ СДЕЛАНО

| Поле | Тип | Default | Описание |
|---|---|---|---|
| `id` | UUID | `gen_random_uuid()` | PK |
| `user_id` | BIGINT | NOT NULL, FK → `users.id` | Пользователь |
| `habit_id` | UUID | NOT NULL, FK → `habits.id` | Клуб, к которому привязана характеристика |
| `value` | BIGINT | 0 | Текущее значение характеристики |
| `last_checkin_at` | TIMESTAMPTZ | NULL | Дата последнего успешного чек-ина в этом клубе |
| `is_frozen` | BOOLEAN | false | Заморожена ли характеристика |
| `frozen_at` | TIMESTAMPTZ | NULL | Когда заморожена |
| `frozen_reason_text` | VARCHAR(256) | `'Отказался расти дальше'` | Текст при заморозке |
| `created_at` | TIMESTAMPTZ | `now()` | |
| `updated_at` | TIMESTAMPTZ | `now()` | |

**Unique index:** `(user_id, habit_id)` — одна характеристика на клуб у пользователя.

**CHECK constraints:**
- `value >= 0` (никогда не уходит в минус).
- `is_frozen = false OR frozen_at IS NOT NULL` (если заморожено — дата обязательна).
- `is_frozen = true OR frozen_at IS NULL` (если не заморожено — даты быть не должно).

**Индексы:**
- `ix_user_stats_user` на `(user_id)` — для профиля.
- `ix_user_stats_habit_value` на `(habit_id, value DESC)` — для лидерборда по характеристике.
- `ix_user_stats_freeze_cron` на `(is_frozen, last_checkin_at)` WHERE `is_frozen = false` — для cron-заморозки.

**Связь с другими таблицами:**
- `user_stats.habit_id` НЕ ссылается на `memberships.id` намеренно — характеристика
  переживает `membership.status = 'left'`. Если пользователь выйдет и вернётся в клуб,
  история восстанавливается (п. 3.4).
- `user_stats.value` — **НЕ** денежная сумма. Это условные «очки дисциплины». В `transactions`
  не пишется. В `bonus_points` (на `users`) не сливается. Это **отдельная** ось прогресса.

### 2.3. Новая таблица `user_statuses` (справочник) — ❌ НЕ СДЕЛАНО

| Поле | Тип | Описание |
|---|---|---|
| `id` | UUID | PK |
| `status_name` | VARCHAR(64) | «Новичок», «Практик», «Мастер», «Легенда» |
| `min_threshold` | INTEGER | Мин. сумма ВСЕХ `user_stats.value` для получения |
| `icon_url` | VARCHAR(512) | Иконка/бейдж |
| `sort_order` | INTEGER | Порядок отображения (UNIQUE) |

**Семя (должна быть отдельная миграция `009_user_statuses_seed.py`):**

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

### 3.1. Создание клуба (через `/admin/v1/habits`) — ✅ УЖЕ СДЕЛАНО

Куратор (`users.id`) указывает: фото, Telegram-инвайт, название, описание,
`stat_name` (обязательно, не пустое), `stat_icon`, окно чек-ина, формат подтверждения
(`proof_type`), `price_month`, `penalty_amount`, `stat_gain_per_checkin` /
`stat_loss_per_miss` (по умолчанию 2/1), лимит участников.

**Где валидируется:** `apps/backend/app/services/habit_service.py:268-388`
(все `_validate_*` функции).

**Применяемые правила (из реального кода):**

| Поле | Правило | Код ошибки (`HabitValidationError.code`) |
|---|---|---|
| `title` | непустой после strip, 3–128 | `habit_title_required` / `habit_title_too_short` / `habit_title_too_long` |
| `stat_name` | непустой после strip, ≤ 64 | `habit_stat_name_required` / `habit_stat_name_empty` / `habit_stat_name_too_long` |
| `stat_icon` | 1–16 символов | `habit_stat_icon_type` / `habit_stat_icon_length` |
| `telegram_invite_link` | NULL или regex `^https://(t\.me\|telegram\.me)/[A-Za-z0-9_+\-/]+$` | `habit_invite_link_format` |
| `timezone` | IANA через `ZoneInfo(tz)` | `habit_timezone_required` / `habit_timezone_invalid` |
| окно чек-ина | `start < end` | `habit_window_required` / `habit_window_order` |
| `price_month`, `penalty_amount` | `int > 0` (INTEGER копейки) | `habit_price_invalid` / `habit_penalty_invalid` |
| `stat_gain_per_checkin`, `stat_loss_per_miss` | `int > 0` | `habit_stat_gain_invalid` / `habit_stat_loss_invalid` |
| `member_limit` | NULL или `int > 0` | `habit_member_limit_invalid` |
| `chat_id` | уникален среди клубов | `habit_chat_id_duplicate` |

**`is_active` в POST не принимается** (Pydantic-схема не содержит поле, `HabitService.create` всегда ставит `False`). Активация — отдельный `POST /admin/v1/habits/{id}/activate`.

### 3.2. Начисление характеристики при успешном чек-ине — ❌ НЕ СДЕЛАНО

**Точка вызова:** внутри `apps/backend/app/services/checkin_service.py:51-128`
`process_checkin()`. После успешного `INSERT INTO checkins` (через
`CheckinRepository.get_or_create_done`, который вернул `created=True`) — увеличиваем
`user_stats.value`. Один DB round-trip + та же транзакция.

**Действия:**
1. `SELECT ... FOR UPDATE` на строку `user_stats(user_id, habit_id)` (или `INSERT` если
   не существует — функция `get_or_create_for_update` в репозитории).
2. Если `is_frozen = true`: разморозить (`is_frozen = false`, `frozen_at = NULL`).
3. `value = value + habit.stat_gain_per_checkin`.
4. `last_checkin_at = NOW()`.
5. `updated_at = NOW()`.

**Идемпотентность:** если чек-ин за этот день уже существует
(`uq_checkins_membership_date`), `process_checkin` возвращает `created=False` (см.
`checkin_service.py:98-107`) и **не вызывает** инкремент `user_stats`. Повторное
сообщение в чат не даёт двойной характеристики — это та же защита, что от двойного штрафа.

**Контракт инварианта:** если в Фазе B `process_checkin` падает на инкременте
`user_stats` после успешного `INSERT checkin`, оба откатываются в одной транзакции.
Это согласуется с правилом проекта «одна транзакция = один handler»
(`docs/04-code-standards.md:11-49`, `AGENTS.md:56`).

### 3.3. Списание характеристики при штрафе — ❌ НЕ СДЕЛАНО

**Точки вызова (в Фазе B):**
- `apps/backend/app/services/penalty_service.py:65-179` `apply_catch()` — для `PenaltyReason.CAUGHT`.
- `apps/backend/app/services/penalty_service.py:181-253` `apply_window_expired()` — для `PenaltyReason.WINDOW_CLOSED_NO_CATCH`.

В обоих случаях — **в той же транзакции** с штрафом, после `flush()` пенальти и
транзакции, до `commit()`.

**Действия:**
1. `SELECT ... FOR UPDATE` на `user_stats(user_id, habit_id)`.
2. `value = GREATEST(0, value - habit.stat_loss_per_miss)` — никогда не уходит в минус.
3. `updated_at = NOW()`.

**При `reason = 'caught'`:** списание характеристики происходит **независимо** от
`catcher_membership_id`. Ловить другого — это его буст, не твой щит.

**При `suspicious_pairs`:** даже если `catcher_bonus_points` не начислен
(см. `penalty_service.py:116` `grant_catcher_bonus = not _is_suspicious(...)`,
`bonus_service.py:75-88`), `user_stats.value` нарушителя всё равно уменьшается.
Дисциплина не ослабляется (это согласуется с п. 4.5 `docs/06-data-model.md`:
«штраф списывается как обычно»).

### 3.4. Заморозка характеристики при неактивности — ❌ НЕ СДЕЛАНО

**Условие (worker `freeze_inactive_stats`):** ежедневная задача проверяет все
`user_stats WHERE is_frozen = false AND last_checkin_at < NOW() - INTERVAL '30 days'`.

**Действия:**
1. `is_frozen = true`.
2. `frozen_at = NOW()`.
3. `frozen_reason_text = 'Отказался расти дальше'` (по умолчанию; в будущем — поле в `habits`).
4. `value` сохраняется, не сбрасывается.

**Расписание:** cron раз в сутки в `04:00 UTC` (до `expire_bonus_points_daily @ 03:00` и
`close_season_daily @ 05:00` — см. `apps/worker/worker/celery_app.py:84-99`). На проде
Celery Beat **уже работает**, надо только добавить schedule + task.

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

### 3.5. Отображение персонажа в профиле — ❌ НЕ СДЕЛАНО

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

**Маршрут должен быть в `apps/backend/app/api/v1/character.py`** (новый файл),
подключается в `apps/backend/app/main.py` рядом с другими `/api/v1/*` роутами.

### 3.6. Админский флоу создания клубов (Owner only) — ✅ УЖЕ СДЕЛАНО

#### 3.6.1. Архитектура

- **Основной бот** `@PrideClubBot` — пользовательский флоу: `/start`, чек-ины,
  штрафы, пополнение.
- **Админ-бот** `@PrideClubAdminBot` — отдельный Mini App `https://admin.prideclub.fun`.
- Контур backend: `/admin/v1/*`. Авторизация: `OWNER_TELEGRAM_ID` из `.env`
  (`core/config.py:36`).

#### 3.6.2. Контур авторизации `/admin/v1/*`

Реализован в общем `AuthMiddleware` (`apps/backend/app/core/middleware.py:78-110`),
не в отдельном файле. Цепочка:

1. Проверка `X-Telegram-Init-Data` через `validate_init_data` с токеном
   `settings.bot_token_admin` (если задан) или fallback на `settings.bot_token`.
2. Сравнение `tg_user.id == settings.owner_telegram_id`.
3. Если не совпадает → `403 NotOwnerError` (`core/exceptions.py:100-102`,
   code=`not_owner`).
4. Если `OWNER_TELEGRAM_ID=0` (не задан в .env) → `503 {"code":"admin_disabled"}`.

`user_id` берётся ТОЛЬКО из `request.state.telegram_user` (как везде) — никаких
параметров `user_id` в теле/querystring.

Все админ-действия логируются через `HabitService.create/update/set_active/archive/restore`
с `extra={"admin_id": ..., "habit_id": ...}`. PII (first_name, username) не логируется —
только `user_id`/`admin_id` (числовые).

#### 3.6.3. Эндпоинты — ✅ ВСЕ СДЕЛАНЫ

Все в `apps/backend/app/api/admin/v1/habits.py`:

| Метод | Путь | Файл:строка | Что |
|---|---|---|---|
| `POST` | `/admin/v1/habits` | `habits.py:79-111` | Создать клуб (всегда `is_active=false`) |
| `GET` | `/admin/v1/habits` | `habits.py:114-126` | Список клубов (все, включая архив) |
| `GET` | `/admin/v1/habits/{id}` | `habits.py:129-142` | Детали клуба + `active_members_count` |
| `PATCH` | `/admin/v1/habits/{id}` | `habits.py:145-161` | Частичное обновление |
| `POST` | `/admin/v1/habits/{id}/activate` | `habits.py:164-182` | Тумблер `is_active` |
| `POST` | `/admin/v1/habits/{id}/archive` | `habits.py:185-198` | Soft-delete |
| `POST` | `/admin/v1/habits/{id}/restore` | `habits.py:201-213` | Снять архив |

#### 3.6.4. Создание клуба: правила

`POST /admin/v1/habits` создаёт клуб **всегда с `is_active = false`** (явно ставится
в `HabitService.create`, `habit_service.py:107`). Поле `is_active` отсутствует
в `AdminHabitCreateRequest` (`schemas/__init__.py:73-95`).

**Тело запроса** — все поля `AdminHabitCreateRequest` (см. §3.1 выше). Пример:

```json
{
  "title": "Планка 30 мин",
  "description": "Держим планку 30 минут каждый день",
  "photo_url": "https://...",
  "telegram_invite_link": "https://t.me/+abcdef",
  "stat_name": "Эстетика тела",
  "stat_icon": "💪",
  "chat_id": -1001234567890,
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

**Все ошибки — через `HabitValidationError` (`core/exceptions.py:105-107`,
status_code=400, code=`habit_validation` + конкретный code из таблицы §3.1).**

#### 3.6.5. Telegram-инвайт: кто создаёт чат

Владелец создаёт группу в Telegram вручную и передаёт инвайт-ссылку через форму
создания. Бот **не создаёт** чаты через `createChat`. `telegram_invite_link` —
отдельное редактируемое поле; если группа пересоздана — админ делает PATCH с новой
ссылкой. У участников ссылка отображается в Mini App как «Перейти в чат клуба».

#### 3.6.6. Гейт `is_active` на стороне пользователя — ✅ УЖЕ СДЕЛАНО

Все публичные запросы фильтруют на уровне репозитория:

| Запрос | Где фильтр | Что отдаётся |
|---|---|---|
| `GET /marketplace` | `HabitRepository.list_with_member_counts` (`habit_repository.py:67-76`) | только `is_active=true AND archived_at IS NULL` |
| `GET /me/habits` | `HabitRepository.list_for_user` (`habit_repository.py:78-90`) | `is_active=true` для пользователя |
| `GET /habits/{id}/today` | `CheckinService.get_today_status` (`checkin_service.py:134-136`) | `raise HabitArchivedError()` если `archived_at IS NOT NULL` |
| Worker `close_catch_window.run_for_active_habits` | `HabitRepository.iter_active` (`habit_repository.py:38-58`) | стриминг только активных неархивных |

**`POST /habits/{id}/join`** — проверка `habit.is_active`/`archived_at` нужна, в
текущем коде MembershipService.join бросает `HabitInactiveError`/`HabitArchivedError`
(исключения уже определены в `core/exceptions.py:110-117`).

#### 3.6.7. Заморозка финансовых полей после первого вступления — ✅ УЖЕ СДЕЛАНО

Реализовано в `HabitService.update` (`habit_service.py:182-195`):

```python
_FROZEN_AFTER_FIRST_MEMBER_FIELDS = frozenset({"price_month", "penalty_amount"})

protected_fields = _FROZEN_AFTER_FIRST_MEMBER_FIELDS & set(fields.keys())
if protected_fields:
    active_members = await self._habit_repo.count_active_members(habit_id)
    if active_members > 0:
        raise HabitValidationError(
            "Финансовые поля заморожены: в клубе уже есть активные участники. "
            "Создайте новый клуб и переведите участников.",
            code="habit_financial_fields_frozen",
        )
```

`count_active_members` (`habit_repository.py:110-118`) считает `memberships WHERE
habit_id = :id AND status != 'left'`. После первого вступления — поля
`price_month`/`penalty_amount` не редактируются (UI скрывает, см.
`apps/frontend/src/admin/pages/HabitEditForm.tsx`).

Остальные поля (`title`, `description`, `photo_url`, `telegram_invite_link`, `stat_*`,
окно чек-ина) — редактируются всегда.

#### 3.6.8. Soft-delete (`archive`) — ✅ УЖЕ СДЕЛАНО

`HabitService.archive` (`habit_service.py:208-225`):
1. `habit.archived_at = now()` + `habit.is_active = False` (атомарно в
   `HabitRepository.archive`, `habit_repository.py:132-135`).
2. Все активные `memberships` остаются как есть (status не меняется).
3. Клуб исчезает из `/marketplace` (фильтр `archived_at IS NULL` в репозитории).
4. Чекин-история, штрафы и балансы участников сохраняются в БД.
5. `GET /habits/{id}/today` → `HabitArchivedError` (404, code=`habit_archived`).
6. Возвращается `200 {"ok": true, "archived_at": "..."}` (`AdminHabitActionResponse`).

`restore` (`habit_service.py:227-239`):
1. `habit.archived_at = None` (`HabitRepository.restore`, `habit_repository.py:137-140`).
2. `is_active` остаётся `false` — админ явно активирует через `/activate`.

`set_active(is_active=True)` (`habit_service.py:241-265`):
- Если клуб в архиве → `HabitArchivedError()` (нельзя активировать архивный).
- Если состояние уже совпадает — no-op.

**Hard delete (`DELETE FROM habits ...`)** — **запрещён** на уровне репозитория
(`HabitRepository` не имеет метода `delete`). Это защищает FK-цепочку
`transactions → penalties → checkins`.

#### 3.6.9. Миграция — ✅ УЖЕ СДЕЛАНА

Файл `apps/backend/alembic/versions/007_habit_admin_fields.py` (revises `006_suspicious_pairs_index`):

```python
op.add_column("habits", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
op.create_index(
    "ix_habits_active",
    "habits",
    ["is_active"],
    postgresql_where=sa.text("is_active = true AND archived_at IS NULL"),
)
```

`ix_habits_active` — частичный индекс, обслуживает горячий путь `/marketplace`.

#### 3.6.10. ENV-переменные — ✅ УЖЕ В КОМПОЗЕ

В `infra/docker-compose.yml:18-19` и `apps/backend/app/core/config.py:36-37`:

```bash
# .env на сервере, chmod 600
OWNER_TELEGRAM_ID=123456789          # ID владельца платформы
BOT_TOKEN_ADMIN=...                  # токен @PrideClubAdminBot (опционально, fallback на BOT_TOKEN)
ADMIN_WEBAPP_URL=https://admin.prideclub.fun
ADMIN_MINI_APP_SHORT_NAME=admin      # Short name в BotFather для админского Mini App
```

Токены **никогда** не попадают в репозиторий, логи или документацию. Если случайно
попали в чат — немедленно отзывать через BotFather `/revoke` (см.
`docs/07-security-and-ops.md:270-278`).

### 3.7. Лидерборд внутри клуба по характеристике — ❌ НЕ СДЕЛАНО

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

**Новый таб в существующем `LeaderboardPage`** (apps/frontend/src/pages/...): «📊 Характеристика»
рядом с «🔥 Серии / 🎯 Ловцы / 💀 Позор». Таб показывается только если в текущем клубе
есть хотя бы 1 `user_stats` с `value > 0` (иначе empty state).

**Реализация в коде:** добавляется роут в `apps/backend/app/api/v1/leaderboard.py`
(уже существует для streak/catches/shame).

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

## 5. Конфигурация по умолчанию (выносим в `core/constants.py`) — ❌ НЕ СДЕЛАНО

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

## 6. Реализация (декомпозиция по слоям) — актуальная

### 6.1. Backend

| Слой | Файлы | Статус |
|---|---|---|
| `models/` | `habit.py` (поля — есть), `user_stats.py`, `user_status.py` | `habit.py` ✅ / остальное ❌ |
| `migrations/` | `007_habit_admin_fields.py`, `008_character_and_club_fields.py`, `009_user_statuses_seed.py` | 007+008 ✅ / 009 ❌ |
| `repositories/` | `user_stats_repository.py` (с `lock_for_update`, `get_or_create_for_update`, `iter_for_freeze_cron`, `iter_for_leaderboard`) | ❌ |
| `services/` | `character_service.py` (`get_character`, `increment_on_checkin`, `decrement_on_penalty`, `apply_freeze`, `get_leaderboard`); изменения в `checkin_service.py` (вызов increment), `penalty_service.py` (вызов decrement) | `character_service.py` ❌ / правки ❌ |
| `api/v1/` | `character.py` (`GET /character/me`), расширение `leaderboard.py` (`GET /leaderboard/stat?habit_id=`) | ❌ |
| `api/admin/v1/` | `habits.py` (POST/GET/PATCH, `/activate`, `/archive`, `/restore`), `__init__.py` | ✅ |
| `core/` | `config.py` (`OWNER_TELEGRAM_ID`, `BOT_TOKEN_ADMIN`), `constants.py` (`CharacterConfig`), `middleware.py` (owner-gate в общем AuthMiddleware) | ✅ / `CharacterConfig` ❌ |
| `tasks/` (worker) | `freeze_inactive_stats.py`, регистрация в `celery_app.py` | ❌ |
| `alembic/versions/` | `007_habit_admin_fields.py` ✅, `008_character_and_club_fields.py` ✅, `009_user_statuses_seed.py` ❌ | миграция 009 ❌ |

**Инварианты реализации (применимы к будущим правкам):**
- `character_service` инжектит `UserStatsRepository` через конструктор (DI), не создаёт
  сессию внутри (правило проекта, `docs/04-code-standards.md:11-49`).
- `increment_on_checkin` / `decrement_on_penalty` вызываются из существующих сервисов
  **в той же транзакции** (не открывают свою). Если `process_checkin` откатится —
  откатится и инкремент `user_stats`. Сервисы НЕ вызывают `session.commit()`
  (см. `habit_service.py:1-16` docstring — это уже правило для всех).
- `freeze_inactive_stats` worker использует bulk update с `LIMIT 1000` за один проход,
  идемпотентный (повторный запуск не дублирует заморозку — `is_frozen` уже `true`).

### 6.2. Frontend — ❌ ВСЁ НЕ СДЕЛАНО

Подключение к API — **отдельная задача** (см. `docs/09-prod-readiness.md:127-137`).
Сейчас фронт имеет базовые страницы и админку (`apps/frontend/src/admin/` +
`apps/frontend/admin.html`).

| Слой | Новые файлы (для Фазы B) |
|---|---|
| `shared/api/` | `characterApi.getMe()`, `characterApi.leaderboard(habitId)` |
| `shared/hooks/` | `useCharacter()`, `useCharacterLeaderboard(habitId)` |
| `pages/` | `Character/CharacterPage.tsx` (или вкладка в Profile) |
| `shared/ui/` | `StatCard`, `StatusBadge` (расширение существующего), `LevelUpToast` |

---

## 7. Тест-план (Definition of Done)

### Unit (pytest) — все ❌ не написаны

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

## 8. Открытые вопросы

### Решены в v2.5 (и подтверждены в коде):

1. ✅ Деньги — `INTEGER` (без `Decimal`).
2. ✅ `clubs` не вводится, расширяем `habits`.
3. ✅ `weekly_n` — отдельное ТЗ позже.
4. ✅ Пауза членства и заморозка характеристики — две независимые механики.
5. ✅ Лидерборд по характеристике — отдельный таб, не заменяет streak/catches/shame.

### Остаются на будущее:

1. **Полное удаление `user_stats`** при `is_frozen=true` дольше N месяцев (например, 6)?
   Сейчас: **никогда не удаляем**, чтобы при повторном вступлении история восстановилась.
2. **Статусы — per-club или глобальные?** Сейчас: **глобальные** по сумме всех характеристик
   (`SUM(user_stats.value)`). Это согласуется с v1 ТЗ, но может потребовать
   пересмотра, если игроки будут «фармить» лёгкие клубы.
3. **Приватность лидерборда характеристики** — возможность скрыть своё значение?
   Сейчас: **нет**, все участники клуба видят всех (как в streak/catches).

---

## 8.1. Что НЕ вошло в этот ТЗ (техдолг до Фазы B)

Реинвентаризация 22.07.2026 14:45 — против фактического кода `main@64f231c`.
Только то, что **реально блокирует чистый старт Фазы B** или приведёт к каше,
если отложить. Подано в порядке «делать перед Фазой B».

| # | Файл:строка | Что | Приоритет | Блокирует Фазу B? |
|---|---|---|---|---|
| **T1** | `apps/backend/app/services/penalty_service.py:275-288` | `_parse_limit()` — приватная функция парсинга rate-limit spec (`10/10s`). Дублирует логику из `http_rate_limiter.py`. Вынести в `core/utils.py::parse_rate_limit_spec()` и заменить оба места на импорт. | 🟡 P1 ✅ сделано | нет, но обязательно перед T2 — иначе каша |
| **T2** | `apps/backend/app/services/penalty_service.py:255-272` | `_is_suspicious()` — SQL прямо в сервисе, лезет в `models/auxiliary.SuspiciousPair`. Нарушает `docs/04-code-standards.md` (запросы только в репозитории). Перенести в `SuspiciousPairsRepository.lookup_flagged(a, b) -> bool`. После T2 у `penalty_service` упростится constructor. | 🟡 P1 | да, если будем трогать penalty_service для decrement_on_penalty |
| **T3** | `apps/backend/app/services/bonus_service.py:36-52` + `:130-135` | Конструктор принимает 4 опц. lookup-коллбэка (`penalty_lookup`, `user_lookup`, `rule_lookup`, `suspicious_blocker`). Лишний `if self._session is not None:` перед `await self._session.flush()` (конструктор уже требует `AsyncSession` — ветка всегда true). Переделать на fakes-based DI (как `tests/fakes.py` для habit_service): инжектить `PenaltyRepo / UserRepo / BonusRuleRepo / SuspiciousPairsRepo` явно, а не через lookup-коллбэки. | 🟡 P1 | косвенно — мешает читаемости reward-цепочки, в которую Фаза B добавит stat-points |
| **T4** | `apps/backend/app/services/checkin_service.py:156-188` | `_compute_streak` — SQL прямо в сервисе (полный `select` по `Checkin` в сервисном методе). Вынести в `CheckinRepository.get_recent_dates(membership_id, up_to, limit=90) -> list[date]`. Цикл в Python оставить в сервисе. | 🟡 P1 | да — иначе Фаза B в `checkin_service` положит ещё один `select`, и будет неразборчиво |
| **T5** | `apps/worker/worker/tasks/process_penalty.py:47-58` | `redis_port=None` оставлен — без него `apply_catch` пропускает rate-limit (`if self._redis is not None`). Это fail-disabled. Сейчас оправдано тестами (Redis не поднимаем), но в проде должна быть явная опция. Решение: в worker-обёртке `_build_production_redis_port()` упасть на `None` (raise + Celery retry), а не идти без rate-limit. | 🟢 P2 | нет |
| **T6** | `apps/worker/tests/conftest.py:98-145` | `_remap_postgres_types_for_sqlite()` мутирует модели глобально на импорт модуля (строка 145 — вызов сразу после объявления). При Фазе B добавится новая модель `UserStats` и `UserStatus` — **забыть положить её в список `models` на строках 120-133 означает, что `tbl.create` упадёт с `TypeError: SQLite does not support type UUID/JSONB/INET`**. Действие: расширить список, не исправлять сам механизм (он работает, хоть и мутирует). | 🟢 P2 | да — критично для тестов Фазы B |
| **T7** | `infra/docker-compose.yml:131` (`worker`) | `worker.mem_limit: 640m`. Сейчас работает. Фаза B добавит `freeze_inactive_stats` cron + новые bulk-операции; worker может OOM-нуть, как это было с ботом (`commit d3adac9` поднял mem_limit до 768m). Поднять превентивно до `768m` / `memswap_limit: 1024m` — как у бота. | 🟢 P2 | косвенно (без теста под нагрузкой) |
| **T8** | `apps/backend/tests/conftest.py` | Аналог T6 для backend — `_remap_postgres_types_for_sqlite` (или похожая логика) + нужен аналогичный список моделей. Сейчас backend тесты проходят (`test_admin_habits_api.py`, `test_habit_gates.py`) через свой файл. Проверить, что Фаза B-тесты наследуют правильный паттерн. | 🟢 P2 | да — новые `test_character_*` сломаются без этой проверки |
| **T9** | `docs/09-prod-readiness.md` §3 | 4 пункта техдолга частично или полностью неактуальны после U1–U7. Полная перепись не нужна, но таблица со ссылками на ветхое — удалить. | 🟢 P2 | нет |
| **T10** | `apps/backend/app/core/constants.py` | Сейчас `CharacterConfig`-блока нет. Перед Фазой B его **лучше не создавать заранее** — без него проще принимать решения по умолчанию (по §2 TZ). Когда пишете `character_service.py` — тогда и вносите единым патчем. | (информация) | — |
| **T11** *(deferred)* | `apps/backend/app/services/penalty_service.py:48, 85` | Legacy ruff-errors, оставшиеся после T1 (не мои — были в `main@64f231c`): F821 `Any` без импорта в сигнатуре `__init__` (строка 48); F841 + E501 на неиспользуемой `idempotency_key = ...` (строка 85). Скорее всего заготовки под будущую `SuspiciousPairsService`-интеграцию (есть docstring «для авто-flag»). E501 на 112 уйдёт сам при T2 (станет короче). Можно почистить в одной PR после Фазы B. | 🟢 P3 | нет |

### Чеклист «можно стартовать Фазу B»

Когда T1–T4 закрыты:

- [ ] `_parse_limit` больше нет в `penalty_service.py` (только импорт).
- [ ] `_is_suspicious` больше нет в `penalty_service.py` (только `await self._suspicious_repo.lookup_flagged(...)`).
- [ ] `bonus_service` принимает 2 репозитория, а не 4 коллбэка.
- [ ] `checkin_repository.get_recent_dates` существует, `checkin_service._compute_streak` состоит из импорта + Python-цикла.
- [ ] `make test` зелёный.

T6 (список моделей) и T8 (backend conftest) — **обязательная часть** коммита с 009 миграцией, иначе новые тесты падают с непонятным traceback.

### Что осознанно НЕ включаем в этот список

- «Переписать `bonus_service` на отдельный `BonusRewardPolicy` strategy» — это уже AGENTS.md-tier задача, не блокер Фазы B.
- «Тесты на `process_penalty` без поднятия Redis» — T5 покрывает.

---

## 8.2. Журнал изменений ТЗ

- **v2.5 (22.07.2026 13:50)** — исходная версия от AI-ассистента. Содержит §8.1
  «Аудит 22.07» и §8.2 «Hardening U1–U7». **Не отражает** то, что §3.6 «Админский
  флоу» уже частично реализован на `main` (`e5e368f feat(backend): admin club
  management + character fields`). Миграции 007/008 переименованы относительно
  того, что в TZ.
- **v3.0 (22.07.2026 14:20)** — синхронизация с кодом после инвентаризации:
  - §0 «Статус реализации» — что готово/что нет (✅/❌) со ссылками на файлы.
  - §3.6 переписан в «уже сделано» — со ссылками на `habit_service.py:...`,
    `habit_repository.py:...`, `core/middleware.py:...`.
  - §3.1 — таблица правил валидации из реального кода (с кодами ошибок).
  - §6 — таблица файлов с фактическим статусом ✅/❌.
  - Добавлен §8.1 «Технический долг вне Фазы B» (10 пунктов).
  - §2 «Структура данных» — переходы к актуальным именам миграций
    (`008_character_and_club_fields.py` вместо `007_...` из v2.5).
  - Конкретизированы ENV-переменные (уже в docker-compose).
  - Frontend §6.2 — отмечено что admin Mini App уже сделан (`apps/frontend/src/admin/`,
    `apps/frontend/admin.html`).

---

## 9. Как пользоваться этим ТЗ в другом чате

Скопировать в новый чат:

1. **Весь этот файл** (`TZ_kharakteristiki_personazha.md`) — даёт полную картину ТЗ
   в актуальном состоянии.
2. **`AGENTS.md`** (в корне репо) — правила проекта, описание архитектуры и ссылка
   на `docs/archive/`.
3. **`docs/04-code-standards.md`** — паттерны кода (DI, исключения, константы).
4. **`docs/06-data-model.md`** — схема БД, антифрод, идемпотентность.
5. **`docs/09-prod-readiness.md`** — статус бэкенда, чеклист до прода.

В новом чате первым делом сказать: **«Фаза B в процессе. Из §6.1 не сделано: models/user_stats.py,
models/user_status.py, repositories/user_stats_repository.py, services/character_service.py,
api/v1/character.py, alembic/versions/009_user_statuses_seed.py, apps/worker/worker/tasks/freeze_inactive_stats.py.
Начни с миграции 009 + models. Перед правкой CheckinService и PenaltyService — сначала
закрой техдолг §8.1 (T1, T2, T4, T6), иначе наслоишь кашу.»**

Полный контекст для нового AI-агента — `AGENTS.md` + `TZ_kharakteristiki_personazha.md`
(этот файл) + `.kilo/skills/habit-club-dev/SKILL.md` (если доступен).
