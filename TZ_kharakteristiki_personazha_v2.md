# Техническое задание
## Модуль «Персонаж и характеристики» (геймификация привычек) — **v2.0**

> **Snapshot 2026-08-21 — глобальные характеристики через справочник `stat_definitions`.**
>
> Версия 3.1 (22.07.2026) этого ТЗ — **superseded этим документом** (см. `TZ_kharakteristiki_personazha.md`).
> Все ссылки на «per-club» / `UserStats(habit_id)` / `icon_url` / статусную лестницу из 4 ступеней —
> **НЕ актуальны**, актуальные утверждения ниже.
>
> **Дата:** 2026-08-21. **Согласовано:** Дмитрий + AI-ассистент, продуктовое решение phase-3-v2 recon.
> **План имплементации:** `EXECUTION-PLAN-2026-08-19.md` §Фаза 3 + разведка Phase 3 от 2026-08-21 в чате.

---

## 0. Главное отличие v1 → v2

| # | v1 (superseded) | v2 (этот документ) |
|---|---|---|
| Scope характеристики | Per-клуб: `user_stats(user_id, habit_id)` | **Глобально по пользователю**: `user_stats(user_id, stat_definition_id)` |
| Название stat | Свободный текст `habits.stat_name: str` | Справочник `stat_definitions` + `habits.stat_definition_id` FK (NULL → ручной выбор админом) |
| Статусы персонажа | 4 ступени (Новичок/Практик/Мастер/Легенда), `icon_url VARCHAR(512)` | **5 ступеней**: 🐣 На старте → 🌊 В потоке → ⚡ На волне → 🔥 В форме → 🐺 Режим зверя, `icon VARCHAR(16)` (emoji) |
| Пороги статусов | 0 / 30 / 150 / 500 | **0 / 30 / 100 / 300 / 700** |
| Freeze условие | 30 дней без чек-ина в конкретном клубе | 30 дней без чек-ина в **ЛЮБОМ** клубе с этой `stat_definition_id` (по `user_stats.last_checkin_at`) |
| Freeze текст | «Отказался расти дальше» | «Характеристика заморожена: нет чек-инов более 30 дней. Сделай чек-ин, чтобы продолжить рост.» |
| Лидерборд | join user_stats ↔ memberships ↔ habits по `(habit_id, user_id)` | Прямой `user_stats WHERE stat_definition_id = habit.stat_definition_id ORDER BY value DESC` |
| Admin API | text input «stat_name» | **dropdown из 8 канонических**, `stat_definition_id` обязателен для новых клубов |
| Legacy fallback в API | — | **НЕТ.** `stat_name`/`stat_icon` остаются в БД, но НЕ принимаются новыми endpoint-ами. |

---

## 1. Справочник `stat_definitions` — 8 канонических

### 1.1 Стартовый набор (seed в миграции 019)

| `slug` | `name` | `icon` |
|---|---|---|
| `intelligence` | Интеллект | 🧠 |
| `strength` | Сила | 💪 |
| `endurance` | Выносливость | 🫁 |
| `balance` | Баланс | 🧘 |
| `energy` | Энергия | ✨ |
| `focus` | Фокус | 🎯 |
| `creativity` | Творчество | 🎨 |
| `connections` | Связи | 🤝 |

### 1.2 Поля таблицы `stat_definitions`

| Поле | Тип | Constraint |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `slug` | VARCHAR(64) | **UNIQUE NOT NULL**, regex `^[a-z][a-z0-9_]*$` |
| `name` | VARCHAR(64) | NOT NULL |
| `icon` | VARCHAR(16) | NOT NULL (эмодзи, 1–16 chars UTF-8) |
| `description` | TEXT | NULL |
| `sort_order` | INTEGER | NOT NULL |
| `is_active` | BOOLEAN | NOT NULL DEFAULT true |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() |
| `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() |

CHECK `length(icon) BETWEEN 1 AND 16` (эмодзи + ZWJ до ~8 multibyte char в UTF-8).

### 1.3 Связь с `habits`

Добавляется колонка:

```sql
ALTER TABLE habits ADD COLUMN stat_definition_id UUID
  REFERENCES stat_definitions(id) ON DELETE RESTRICT;
CREATE INDEX ix_habits_stat_definition
  ON habits(stat_definition_id) WHERE stat_definition_id IS NOT NULL;
```

**NOT NULL** НЕ ставится в миграции 019 — существующие клубы с `stat_name='Дисциплина'` (и другие неканонические) остаются с NULL до явного выбора админом (см. §7).

### 1.4 Backfill внутри миграции 019

ТОЛЬКО точное совпадение по `name`:

```sql
UPDATE habits h
SET stat_definition_id = sd.id
FROM stat_definitions sd
WHERE h.stat_name = sd.name;
```

`Дисциплина` и подобные (без точного мэтча) остаются `NULL` — никаких угадывающих маппингов.

---

## 2. `user_stats` — глобальный счётчик

### 2.1 Схема

| Поле | Тип | Constraint |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `user_id` | BIGINT | NOT NULL FK → `users(id) ON DELETE RESTRICT` |
| `stat_definition_id` | UUID | NOT NULL FK → `stat_definitions(id) ON DELETE RESTRICT` |
| `value` | BIGINT | NOT NULL DEFAULT 0, CHECK `value >= 0` |
| `last_checkin_at` | TIMESTAMPTZ | NULL |
| `is_frozen` | BOOLEAN | NOT NULL DEFAULT false |
| `frozen_at` | TIMESTAMPTZ | NULL |
| `frozen_reason_text` | VARCHAR(256) | NULL |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() |
| `updated_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() |

**UNIQUE: `(user_id, stat_definition_id)`** — критично. Два клуба с одной stat пишут в одну и ту же строку `user_stats`.

CHECK: `(is_frozen = false AND frozen_at IS NULL) OR (is_frozen = true AND frozen_at IS NOT NULL)`.

### 2.2 Индексы

- `ix_user_stats_user (user_id)` — для `/character/me`.
- `ix_user_stats_stat_value (stat_definition_id, value DESC)` — для `/leaderboard/stat`.
- `ix_user_stats_freeze_cron (stat_definition_id, last_checkin_at) WHERE is_frozen = false AND last_checkin_at IS NOT NULL` — частичный, для cron-заморозки.

### 2.3 Семантика increment/decrement

| Операция | Где вызывается | Семантика |
|---|---|---|
| **Increment** | После успешного `CheckinService.process_checkin` (только при `created=True`) | `value += habit.stat_gain_per_checkin`. Если `is_frozen=true` → разморозить (`is_frozen=false, frozen_at=NULL`), `last_checkin_at=now()`. |
| **Decrement** | После `flush penalty` в `PenaltyService.apply_catch` | `value = GREATEST(0, value - habit.stat_loss_per_miss)`. Floor на 0 (никогда не уходит в минус). |
| **Skip** (silent) | Если `habit.stat_definition_id IS NULL` | Skip + WARN-лог `stat_skipped_no_definition`. Чек-ин/поимка всё равно работают — только stat-рост не происходит. Это позволяет rollout без обязательного перевыбора всех клубов в один день. |

### 2.4 Идемпотентность

- **Increment:** идемпотентность через `created` флаг из `checkin_repo.get_or_create_done` (на уровне `CheckinService`). Повторный чек-ин за день → `created=False` → increment НЕ вызывается.
- **Decrement:** защищён Penalty уже UNIQUE-индексом `(membership_id, date, reason)` на уровне БД.
- **`user_stats.value` НЕ пишется в `transactions`** (отдельная ось, не деньги).

---

## 3. `user_statuses` — 5 статусов

### 3.1 Seed (5 строк INSERT в миграции 019)

| `status_name` | `min_threshold` | `icon` | `sort_order` |
|---|---:|---|---:|
| На старте | 0 | 🐣 | 1 |
| В потоке | 30 | 🌊 | 2 |
| На волне | 100 | ⚡ | 3 |
| В форме | 300 | 🔥 | 4 |
| Режим зверя | 700 | 🐺 | 5 |

**Итоговая шкала:** 🐣 На старте → 🌊 В потоке → ⚡ На волне → 🔥 В форме → 🐺 Режим зверя.

### 3.2 Схема таблицы

| Поле | Тип | Constraint |
|---|---|---|
| `id` | UUID PK | `gen_random_uuid()` |
| `status_name` | VARCHAR(64) | UNIQUE NOT NULL |
| `min_threshold` | INTEGER | NOT NULL, CHECK `>= 0` |
| `icon` | VARCHAR(16) | NOT NULL (эмодзи) |
| `sort_order` | INTEGER | UNIQUE NOT NULL |
| `created_at` | TIMESTAMPTZ | NOT NULL DEFAULT now() |

### 3.3 Вычисление статуса

`total_value = SUM(user_stats.value WHERE user_id=:id AND (is_frozen=false OR is_frozen=true))`.

> Сумма считается **по ВСЕМ** характеристикам юзера, в т.ч. замороженным
> (заморозка сохраняет value, не обнуляет).

```sql
SELECT MAX(status_name), MAX(min_threshold), MAX(icon), MAX(sort_order)
FROM user_statuses
WHERE min_threshold <= :total_value;
```

`status_info.next_threshold` = `min_threshold` следующей ступени или `NULL` (если текущая — максимальная).
`status_info.next_status` = `status_name` следующей ступени или `NULL`.

---

## 4. Freeze

### 4.1 Условие

Характеристика замораживается, если пользователь не делал чек-ин более 30 дней **ни в одном клубе**, который привязан к этой `stat_definition_id`.

`user_stats.last_checkin_at` обновляется при чек-ине в любом клубе с соответствующим `stat_definition_id` — то есть два клуба с одной stat совместно «кормят» счётчик активности.

### 4.2 Действия freeze-cron (ежедневно в 04:00 UTC)

```sql
-- Идемпотентно: WHERE is_frozen=false AND last_checkin_at < now() - 30 days
UPDATE user_stats
SET is_frozen = true,
    frozen_at = now(),
    frozen_reason_text = 'Характеристика заморожена: нет чек-инов более 30 дней. Сделай чек-ин, чтобы продолжить рост.'
WHERE is_frozen = false
  AND last_checkin_at IS NOT NULL
  AND last_checkin_at < now() - INTERVAL '30 days';
```

Bulk-UPDATE батчами по 1000 записей (одна короткая транзакция на батч, чтобы не блокировать).

### 4.3 Возврат из заморозки

Любой успешный чек-ин в клубе с этой `stat_definition_id` размораживает автоматически (см. §2.3). `value` сохраняется, не сбрасывается.

### 4.4 Независимость от `membership.status = 'paused'`

| Механизм | Причина | Эффект | Снимается |
|---|---|---|---|
| `membership.status='paused'` | депозит = 0 | Чек-ины не принимаются, штрафы не списываются | Пополнением депозита |
| `user_stats.is_frozen=true` | 30 дней без чек-ина | Чек-ины ПРОДОЛЖАЮТ работать, характеристика не растёт/падает | Успешным чек-ином в клубе с этой stat |

Это **две независимые механики**. Клуб может быть `paused`, а характеристика `active` (и наоборот).

---

## 5. Лидерборд в клубе

### 5.1 Endpoint

`GET /api/v1/leaderboard/stat?habit_id={uuid}`.

### 5.2 Семантика

Показывает общий глобальный `stat` участников по характеристике, выбранной у этого клуба. Вклад именно данного клуба не считается.

```sql
SELECT
  m.id AS membership_id,
  m.user_id,
  u.first_name,
  us.value
FROM habits h
JOIN memberships m
  ON m.habit_id = h.id
  AND m.status != 'left'
JOIN user_stats us
  ON us.user_id = m.user_id
  AND us.stat_definition_id = h.stat_definition_id
JOIN users u
  ON u.id = m.user_id
WHERE h.id = :habit_id
ORDER BY us.value DESC, m.user_id ASC
LIMIT 100;
```

**Исключаются** `membership.status = 'left'`. `is_frozen = true` — остаются в рейтинге с пометкой `❄`.

**Антифрод:** `suspicious_pairs` НЕ исключает из этого лидерборда (характеристика своя, не зависит от поимок).

### 5.3 Таб в существующем `LeaderboardPage`

Рядом с `🔥 Серии / 🎯 Охотники / 😴 Лентяи` добавить `📊 Характеристика`. Показывать только если в клубе есть хотя бы 1 `user_stats` с `value > 0` для этой stat.

---

## 6. Admin API — только `stat_definition_id`

### 6.1 Правила (подтверждено 2026-08-21)

- **POST `/admin/v1/habits`** (создание клуба): `stat_definition_id` **обязателен**. Без него — 400 `habit_stat_definition_required`.
- **PATCH `/admin/v1/habits/{id}`** (редактирование): `stat_definition_id` опционален. Если передаётся — может быть как UUID, так и `null` (явный «снять» выбор, например для тестирования).
- **`stat_name` / `stat_icon` НЕ принимаются** и **НЕ изменяются** новыми endpoint-ами. Никакого legacy fallback, никакого авто-маппинга.

### 6.2 Read-only endpoint для dropdown

`GET /admin/v1/stat-definitions` — список активных канонических статов (для заполнения dropdown в admin UI). Owner-gated (как остальные admin endpoints).

---

## 7. Баннер для NULL-клубов (ОБЯЗАТЕЛЬНО в admin UI)

Цель: через месяц НЕ должно быть клубов, которые quietly skip-ают начисления.

### 7.1 Карточка клуба в списке (`HabitsListPage`)

Видимый badge `⚠️ Характеристика не выбрана` (warning-color, всегда показывается если `stat_definition_id IS NULL`).

### 7.2 Форма редактирования (`HabitEditForm`)

Если `stat_definition_id IS NULL` — поле «Характеристика» помечается `⚠️ Обязательно выберите`, валидация на submit блокирует сохранение без выбора.

### 7.3 Форма создания (`HabitCreatePage`)

Dropdown обязателен, default = первая каноническая (например `intelligence`). Нельзя создать NULL-клуб через UI. Серверный 400 — последний рубеж (если кто-то шлёт raw POST без поля).

---

## 8. Чего НЕ вошло в v2 (отложено, отдельные задачи)

| # | Что | Причина / когда |
|---|---|---|
| 1 | `contributing_habits` в `/character/me` (JOIN на habits WHERE stat_definition_id = ...) | Потенциально дорогая агрегация. Добавляется отдельным API-улучшением если UI потребует показать «какие клубы качают Интеллект». См. recon отчёт §1.5 caveat. |
| 2 | DROP COLUMN `habits.stat_name`/`habits.stat_icon` (миграция 021) | Только когда `SELECT count(*) FROM habits WHERE stat_definition_id IS NULL = 0`. В Phase 5/6 (tech debt), НЕ в Phase 3. |
| 3 | `first_name_initial` в лидерборде (ФЗ-152 — только инициал) | Отдельная задача для ВСЕХ табов (`streak`/`catches`/`shame`/`stat`), не для одного. |
| 4 | Удаление `apply_window_expired`/`mark_waived_unable_to_pay` | Deprecated в Phase 8 / manual-catch-2026-08-18, но ещё живут (safe no-op). Техдолг Phase 5. |

---

## 9. Что нужно фронту

### 9.1 Карточка «Мой персонаж» в `ProfilePage`

Краткая сводка: общий `total_value`, текущий `status.name` + `status.icon`, прогресс-бар до `next_threshold`. Кнопка «Открыть персонажа →` ведёт на `/character`.

### 9.2 Страница `/character`

Подробный список всех характеристик (`StatCard` для каждой):
- иконка + название (`stat_icon` + `stat_name`).
- текущее `value`.
- `last_checkin_at`.
- `is_frozen` → приглушённый фон, иконка ❄, `frozen_reason_text`, дата `frozen_at`.
- Прогресс-бар до следующего статуса (пороги статусов → от max(текущий threshold) до next).

Если `total_value < 1` — пустое состояние «Сделай чек-ин, чтобы разблокировать персонажа».

### 9.3 Level-up toast

На клиенте: при изменении `status.name` (между refetch'ами) → toast «Новый статус: {icon + name}» + `Telegram.WebApp.HapticFeedback.impact('medium')`. Не требует SSE.

### 9.4 Таб «📊 Характеристика» в лидерборде

Один tab в `LeaderboardPage` плюс поддержка в `useLeaderboard(habitId, tab)` (уже generic).

### 9.5 Никаких новых цветов вне палитры

`docs/05-ui-ux.md` — фиолет→бирюза для прогресс-баров, коралл для замороженных (по аналогии со штрафами), золото для топ-3 в лидерборде.

---

## 10. Сквозные инварианты (cross-cutting)

1. **Деньги и stat-value** — разные оси. `stat_value` НЕ пишется в `transactions`. Не путать с Phase 1 `catcher_deposit` (это денежная транзакция, stat-decrement — отдельная логика).
2. **`suspicious_pairs` (Phase 1 variant A):** НЕ влияет на stat-decrement. Это независимая механика. Даже при flagged-паре `user_stats.value` нарушителя всё равно уменьшается.
3. **`is_frozen=true WHERE last_checkin_at IS NULL`** — не трогается cron'ом (частичный индекс требует NOT NULL). Это OK: «никогда не делал чек-ин» ≠ «заморожен за неактивность».
4. **`membership.status='left'`** — НЕ удаляет `user_stats`. При повторном `join` — характеристика восстанавливается как есть (та же строка по UNIQUE).
5. **`habit.stat_definition_id IS NULL`** — silent skip в increment/decrement. Чек-ин/поимка работают, только stat-рост не происходит. Админ видит баннер, выбирает характеристику, stat начинает расти со следующего чек-ина/поимки.
6. **Per-habit timezone** — НЕ влияет на stat-расчёты. `last_checkin_at` хранится в UTC, freeze-cron сравнивает с `now() - INTERVAL '30 days'` тоже в UTC.

---

## 11. Тест-план (Definition of Done, ключевые кейсы)

### Unit (pytest, backend)
- `test_increment_on_first_checkin_creates_user_stats` (lazy creation по `(user_id, stat_definition_id)`).
- `test_global_stat_shared_two_clubs_one_user` (race-критический: 2 клуба с `intelligence`, поимка в обоих → одна строка `user_stats` декрементируется дважды).
- `test_increment_unfreezes_frozen_stat`.
- `test_increment_skipped_when_habit_stat_definition_is_null` (silent skip + WARN-лог).
- `test_decrement_with_floor_at_zero` (`value=1, loss=5 → value=0`, не отрицательное).
- `test_freeze_inactive_after_30_days`.
- `test_freeze_idempotent_no_op` (повторный запуск = 0 изменений).
- `test_uniqueness_user_stat_definition` (IntegrityError на INSERT второго user_stats на тот же `(user_id, stat_definition_id)`).
- `test_left_membership_does_not_delete_user_stats`.
- `test_calculate_status_picks_highest_threshold` (sum=100 → «На волне», не «В потоке»).

### Unit (vitest, frontend)
- `CharacterPage empty state` (total_value < 1).
- `CharacterPage single stat` (1 habit + 1 stat).
- `CharacterPage mixed frozen/active` (банер ❄ + обычное состояние).
- `LeaderboardPage tab.stat rendering`.

### Integration (E2E на проде, `scenario_character.py`)
1. Create 2 habits (один stat) + 1 habit (другой stat).
2. Checkin в оба «одинаковых» клуба → `user_stats(stat1).value` инкрементируется дважды.
3. Catcher ловит user в одном из них → `user_stats(stat1).value` декрементируется на ОДИН `loss` (не ноль).
4. Simulate `last_checkin_at` = 30 дней назад → run cron → `is_frozen=true`, новый текст.
5. Checkin снова → `is_frozen=false`, value инкрементится.

### Negative tests
- POST `/admin/v1/habits` без `stat_definition_id` → 400 `habit_stat_definition_required`.
- PATCH `/admin/v1/habits/{id}` с `stat_name` (старым полем) → 422 (отвергается Pydantic, поле больше нет в схеме).
- POST `/admin/v1/habits` с `stat_name="Интеллект"` (старый формат) → 422.

---

## 12. Открытые вопросы на будущее

1. **Лидерборд `total_value` (по всем характеристикам) vs per-habit вклад** — показываем глобальный `total_value` в колонке лидерборда (см. recon §3.7)? Если да — отдельный API endpoint. Сейчас ТЗ не требует.
2. **Удаление `user_stats.is_frozen=true` дольше N месяцев** — сейчас «никогда не удаляем» (восстановление истории при left+rejoin). Оставить так навсегда?
3. **Приватность лидерборда stat** — возможность скрыть своё значение? Сейчас «нет» (как в streak/catches). Если да — отдельная настройка privacy для всех табов сразу.

---

## 13. Связанные документы

- `TZ_kharakteristiki_personazha.md` (v3.1, 22.07.2026) — **superseded**, сохранён для истории.
- `EXECUTION-PLAN-2026-08-19.md` §Фаза 3 + разведка Phase 3 (this recon от 2026-08-21).
- `docs/01-concept.md` — продуктовая концепция (геймификация, мотивация).
- `docs/04-code-standards.md` §11.1 «изменение депозита» — определяет «одна транзакция = один handler».
- `docs/06-data-model.md` — будет расширен в Task 3.11 после деплоя.
- `AGENTS.md` — layered architecture, DI через конструктор, async I/O.
