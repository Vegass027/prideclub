# Pravki — Разведка бизнес-логики на дыры / 2026-08-18

> Snapshot 2026-08-18. Read-only ревизия всей бизнес-логики Privichki.
> Цель — найти места, где пользователь может обмануть систему или несправедливо
> потерять деньги из-за неправильной реализации бизнес-логики. Изменений в коде
> не делалось, только разведка.
>
> **Severity:**
> 🔴 Critical (блокирует прод или даёт прямой финансовый ущерб)
> 🟠 High (финансово-семантическая дыра, требует фикса до роста нагрузки)
> 🟡 Medium (UX/edge-case, стреляет при специфических условиях)
> 🟢 Low (косметика или мёртвый код)
>
> **Охват:** backend (FastAPI), worker (Celery), bot (aiogram), frontend (React/TS).
> Data: миграции 000–015, модели Penalty/Checkin/Membership/User/Transaction/Season.

---

## ⚠️ Верификация находок #1 и #2 (2026-08-18, пост-аудит)

**Противоречие с `Pravki.md` §6.1 / §6.2.** Юзер указал, что эти секции (аудит от 2026-07-23) помечают призовой фонд и ловца-бонусы как **✅ работает корректно**. Моя разведка #1 и #2 противоречит этому. Прогнал верификацию в чистом worktree `feature/qa-batch-2026-08-14` (`HEAD = 7c2591b9`).

**Результат:** оба моих вывода **ПОДТВЕРЖДЕНЫ**, но framing уточнён:

| # | Было сказано в моём recon | Уточнение после верификации |
|---|---------------------------|----------------------------|
| #1 | «`apply_catch_bonus` НИКОГДА не вызывается» | «**НИКОГДА не вызывалось с самого начала** (commit `8fc2b71` от 2026-07-21, первая версия `celery_producer.py`). Это **never-implemented feature**, а не регрессия.» |
| #2 | «`Season.prize_pool` никогда не обновляется» | «**Никогда не обновлялось**. Audit Pravki.md §6.1 описывает цепочку `Penalty → Habit.prize_pool → Season.prize_pool → close_season`, но **средняя стрелка никогда не была реализована**. Audit ошибочно пометил ✅ — он не нашёл этой дыры, потому что просто перечислил endpoint-файлы без проверки передачи данных между ними.» |

**Доказательства (см. детальный разбор ниже):**
- `git log --all -p apps/backend/app/services/celery_producer.py` — `_TASK_NAMES` НИКОГДА не содержал `"apply_catch_bonus"`.
- `git log --all -p apps/worker/worker/tasks/process_penalty.py | grep bonus` — 0 вхождений.
- `git log --all -p apps/backend/app/api/v1/members.py | grep bonus` — 0 вхождений.
- `apps/worker/tests/test_worker_cron_chain.py:84` тестирует цепочку через **прямой импорт `_process`**, минуя broker — тест проходит независимо от того, вызывается ли таск в проде. **Не является доказательством работы в проде.**
- `start_season` определён в `season_service.py:45`, но `grep -rn "start_season" apps/` показывает **только эту строку определения** + mypy cache.
- `Season.prize_pool` НИКОГДА не имел setter/writer — `git show 00884e8` (initial commit) показывает `prize_pool: int default=0`, и ни один последующий commit не добавляет writer.

**Что это значит для Sprint-планирования:**
- Sprint 1 (фикс #1 + #17 + #16) остаётся — это **«доделать незавершённое с самого начала»**, не «починить сломанное».
- Sprint 4 (фикс #2 + #11 + #28) остаётся — но это MVP-фича, не баг-фикс.
- **Требуется апдейт `Pravki.md`** — пометить §6.1 и §6.2 как «✅ не работает» или удалить неверный знак, иначе следующий агент будет введён в заблуждение.
- **Audit-процедуру нужно ужесточить** — описывать файлы недостаточно; нужно проверять «**кто зовёт эту функцию в проде**».

---

## TL;DR — карта дыр

## TL;DR — карта дыр

| # | Дыра | Severity | Влияние |
|---|------|----------|---------|
| 🔴 1 | `apply_catch_bonus` НИКОГДА не вызывается — `bonus_points` не начисляются ловцам | **Крит** | Ловец не получает обещанные баллы. `integrity_check` обходится (orphan'ов нет, потому что `bonus_applied` всегда False). |
| 🔴 2 | Сезоны — мёртвый код: `Season.prize_pool` никогда не обновляется, `start_season` не вызывается, эндпоинта создания нет | **Крит** | Призовой фонд никогда не распределяется. `close_season` всегда находит 0 активных сезонов, а если найдёт — раздаёт 0 ₽. |
| 🔴 3 | Cron `close_catch_window` на `:05` каждого часа ломает «catch window = чек-ин + 1ч» | **Крит** | У участников реально **5 минут** на поимку, а не 1 час. В `Pravki-deposit-sse.md` явно заявлено окно +1ч. |
| 🟠 4 | `enqueue_checkin` пропускает `is_forwarded` в worker, а `proof_validator` не получает `forward_date` — defense-in-depth на forward **не работает** | **Выс** | Пересланный кружок можно протащить через прямой POST на `/internal/checkins/process` (если утечёт service token). |
| 🟠 5 | В `_resolve_catcher_membership_id` `catcher_membership_id` берётся из **любого** активного membership ловца, не обязательно из целевого habit | **Выс** | Атрибуция улова чужому membership → ловец получает leaderboard-очки в клубе, где не состоит. Anti-`suspicious_pairs` не срабатывает (пары в разных habit). |
| 🟠 6 | Порядок проверок в `checkin_service.process_checkin` нарушает canonical priority v2 при race `caught_today + paused` | **Выс** | Юзер с `penalty=true AND status=paused` (штатный сценарий после `apply_catch`) получит в worker `membership_paused`, а не `caught_today`. Семантическая потеря для UI/логов. |
| 🟡 7 | `subscribe_and_join` использует `date.today()` (локальная TZ сервера) вместо `datetime.now(UTC)` | **Ср** | На сервере с TZ ≠ UTC возможна граничная ошибка: оплата в 23:59 локального времени считается за «вчера». Docker compose полагается на default UTC — пока не стреляет, но fragile. |
| 🟡 8 | `apply_catch` не проверяет, что вызывающий — тот же клуб, что и `catcher_membership_id` (может «поймать» в чужом клубе) | **Ср** | UI сейчас берёт `catcher_membership = get_for_user_in_habit(user.id, habit_id)` — фильтрует. Но **API-контракт `internal_penalties.py` это не проверяет**. Сейчас endpoint никем не вызывается, но при починке pay-флоу выстрелит. |
| 🟡 9 | `catch_rate_limiter` Redis-down = потеря лимита (fail-CLOSED через retry, но в момент сбоя возможны race) | **Ср** | Проверено: `process_penalty.run` бросает `RateLimitDisabledError` при `redis_port=None` → Celery retry. Но если Redis доступен, но `INCR` упал в момент — поведение зависит от redis-клиента. |
| 🟡 10 | `publish_catch_event` / `publish_you_were_caught` шлются **отдельно** от начисления ловцу — если публикация упала, юзер не увидит обновление | **Ср** | Финансово безопасно (penalty уже в БД), но UX-несогласованность на 1-2 раунда polling'а. |
| 🟡 11 | `prize_pool` в `Habit` — общий для всех сезонов без изоляции по `Season` | **Ср** | При `close_season` фонд, накопленный за пределами сезона, не отделяется. Фактически не стреляет (см. дыру #2), но при включении сезонов даст неверные суммы. |
| 🟡 12 | `validate_proof_media` принимает `stale_message` window `[-5s, +60s]` — позволяет двойную отправку одного и того же `message_id` (вне 60с — отбивается, но в окне — дубликат пройдёт) | **Ср** | `checkin_id` UNIQUE-индекс всё равно отработает, но бот ответит «Принято» дубля. |
| 🟡 13 | `is_within_checkin_window` не поддерживает окна через полночь (известный FIXME в `habit.py:117`) | **Ср** | Любой клуб с `start=22:00, end=06:00` (ночной) — `03:00` покажет «окно закрыто». `was_joined_after_window` корректно, но `is_within_checkin_window` — нет. |
| 🟡 14 | `penalty_service.apply_catch` использует `await self._session.refresh(violator)` после user-lock — но `refresh` НЕ делает `FOR UPDATE` повторно, и **status может снова устареть** до flush/commit | **Ср** | Сейчас критично защищено тем, что `recompute_pause_status` идёт в той же транзакции. Но если кто-то удалит вызов `recompute_pause_status` — race вернётся. |
| 🟡 15 | `effective_deposit` в `smart renew` (subscribe_and_join шаг 5) — `u.deposit_balance + deposit_amount_kopecks` — НЕ учитывает гонку с конкурентным списанием | **Ср** | На практике user-lock защищает, но при ошибке валидации (effective_deposit >= penalty) — `u.deposit_balance += 0` всё равно, но `dep_tx` создаётся с `amount=0` и `idempotency_key=dep_key`. После retry — вернёт тот же ключ. OK. |
| 🟡 16 | `apply_window_expired` WAIVED-ветка (ACTIVE+deposit=0) пишет маркер, но **не вызывает `recompute_pause_status`** → юзер остаётся ACTIVE с deposit=0 | **Ср** | Штраф не списывается в моменте, но `apply_catch` после topup снимет деньги за прошлый день. **Несправедливая потеря денег.** |
| 🟡 17 | `apply_catch` для `deposit=0` бросает `PenaltyAlreadyProcessedError("deposit_exhausted")` БЕЗ записи WAIVED-маркера | **Ср** | После topup `apply_catch` снимет деньги за прошлый день. **Несправедливая потеря денег.** |
| 🟡 18 | `join` LEFT→ACTIVE reuse **не проверяет deposit** | **Ср** | Юзер с 0 deposit реактивируется, после первого штрафа уходит в PAUSED, не понимая почему. |
| 🟡 19 | `leave` без возврата депозита — деньги остаются на `users.deposit_balance` глобально | **Ср** | UX hole, не финансовая — но документация заявляет «за вычетом техкомиссии», а возврата нет вообще. |
| 🟢 20 | `current_user_db` upsert'ит запись в `users` без `accepted_offer_at` — first-time visitor сразу в БД | **Низ** | Не финансовая, но anti-ФЗ-152: пользователь не выразил согласия, но уже в БД. |
| 🟢 21 | `expire_bonus_points` берёт `bonus_points > 0 AND bonus_points_updated_at < cutoff` — после миграции `bonus_points_updated_at` NULL → нет сгорания | **Низ** | Дыра неактивна, пока `apply_catch_bonus` не работает (см. #1). |
| 🟢 22 | `suspicious_pairs.evaluate_after_catch` определён, но **никогда не вызывается** | **Низ** | Anti-сговор по сути мёртв — `SUSPICIOUS_ASYMMETRY_THRESHOLD=3` недостижим. |
| 🟢 23 | `IntegrityError` handler в `process_penalty` возвращает `duplicate=True` БЕЗ отправки SSE-событий `publish_catch_event` / `publish_you_were_caught` | **Низ** | При гонке двух кэтчеров один не получит broadcast. Пенальти второй раз не спишется (UNIQUE), но визуально «свой» catch не подтвердится. |
| 🟢 24 | `leaders_clubs` отдает `members_count` через `list_active()` — для archived клубов leaderboard недоступен | **Низ** | Дизайн-решение, не дыра. |
| 🟢 25 | `catch_rate_limiter` INCR persistent — если валидация упала **после** `incr_catch` (до списания), пользователь «потратил» квоту впустую | **Низ** | Не financial, но возможен `TooManyCatchAttemptsError` из-за ложных срабатываний. |
| 🟢 26 | `stale_message` window 60s — медленный мобильный Telegram → ложный `stale_message` без объяснения | **Низ** | UX hole, не финансовая. |
| 🟢 27 | `sse_published:checkin:{m}:{d}` 24h TTL — worker re-deliver через 25ч → дубликат SSE frame | **Низ** | Маловероятно, но UX-шум. |
| 🟢 28 | `SeasonStats` не инкрементируется нигде (`streak_days`, `total_penalties_caught`, `total_penalties_received` остаются 0) | **Низ** | Leaderboard по streak/catches считает из `Checkin`/`Penalty` напрямую (см. `leaderboard.py:130-132`), `SeasonStats` не используется. |

---

## Детали по критическим находкам

### 🔴 #1 — `apply_catch_bonus` никогда не вызывается

**Файл:** `apps/backend/app/services/celery_producer.py:21-35`

```python
_TASK_NAMES: dict[str, str] = {
    "checkin": "worker.tasks.process_checkin.run",
    "penalty": "worker.tasks.process_penalty.run",
    "payment": "worker.tasks.process_payment.run",
    "publish_catch_event": "worker.tasks.publish_catch_event.run",
    "publish_you_were_caught": "worker.tasks.publish_you_were_caught.run",
    # ❌ нет "apply_catch_bonus"
}
```

**Файл:** `apps/backend/app/services/penalty_service.py:182` — комментарий упоминает задачу, но `send_task` не вызывается:

```python
# Применяется ли кэтчер-бонус — отдельная проверка suspicious_pairs (см. apply_catch_bonus).
grant_catcher_bonus = not await self._suspicious_repo.lookup_flagged(
    catcher_membership_id, violator_membership_id
)
```

**Файл:** `apps/worker/worker/tasks/process_penalty.py` — после `apply_catch` НЕ шлёт `apply_catch_bonus`.

**Файл:** `apps/backend/app/api/v1/members.py:catch_violator` — после успешного `apply_catch` шлёт только `publish_catch_event` и `publish_you_were_caught`, **НЕ** `apply_catch_bonus`.

#### ⚠️ Верификация: never-implemented, а не регрессия

Прогнал `git log --all -p` по всем релевантным файлам:

**`apps/backend/app/services/celery_producer.py`** — все 4 коммита, которые когда-либо трогали файл:
- `8fc2b71 feat: async Celery queue + worker idempotency + Sentry/Prometheus observability` (2026-07-21, initial)
- `7a39fc1 fix(lint): zero ruff errors + migrate to Annotated[...]`
- `175c3ff feat(realtime): Item 6 — publish_to_habit + worker task publish_catch_event`
- `cb5938d feat(realtime): Item 8 — catch_event + you_were_caught broadcasts`

`git log --all -p -S "_TASK_NAMES"` показывает: при создании файла (commit `8fc2b71`) было ровно 3 ключа (`checkin`, `penalty`, `payment`). Все последующие коммиты только ДОБАВЛЯЛИ `publish_*` ключи. **`"apply_catch_bonus"` не появлялся ни разу во всей истории файла.**

**`apps/worker/worker/tasks/process_penalty.py`** — `git log --all -p | grep -E "apply_catch_bonus|bonus_service|BonusService|send_task.*bonus"` = **0 строк**. Никогда не планировалось send_task отсюда.

**`apps/backend/app/api/v1/members.py`** — `git log --all -p | grep -E "apply_catch_bonus|bonus_service"` = **0 строк**. Когда добавлялись `publish_catch_event` и `publish_you_were_caught` (commits `175c3ff`, `cb5938d`), никто не подумал добавить `apply_catch_bonus` рядом.

**Тест `apps/worker/tests/test_worker_cron_chain.py:84`** — вызывает `apply_bonus` через **прямой импорт `_process`**, не через Celery broker:
```python
from worker.tasks.apply_catch_bonus import _process as apply_bonus
...
bonus_result = await apply_bonus({"catcher_membership_id": ..., "penalty_id": ...})
```
Тест проверяет, что **сама функция работает**, но **не проверяет**, что она вызывается в проде через broker. Это «test the function», не «test the chain». **Вводит в заблуждение — выглядит как доказательство работы цепочки, но это не оно.**

**Эффект в проде:**
1. `Penalty.catcher_bonus_points` ставится = 1 (через `apply_catch`), но это поле фактически нигде не читается.
2. `User.bonus_points` остаётся 0 для всех — лидерборд «Охотники» по `bonus_points` всегда пуст.
3. `penalty.bonus_applied` остаётся `False` → `integrity_check_bonus_transactions` ничего не находит (orphan'ов нет).
4. Anti-`suspicious_pairs` работает на уровне `grant_catcher_bonus` в `apply_catch` (там `catcher_membership_id` обнуляется), но **bonus_service** всё равно никогда не вызывается — даже подозрительные пары не наказываются зачислением, но они и не наказываются **отсутствием** зачисления, потому что зачисления нет вообще.

**Что починить:** добавить `send_task("apply_catch_bonus", ...)` в `process_penalty.run` после `apply_catch` (или после `notify_catch` через отдельную фазу по аналогии с `publish_catch_event`). И добавить `"apply_catch_bonus": "worker.tasks.apply_catch_bonus.run"` в `_TASK_NAMES`. **Также добавить e2e-тест через broker** (прод-обёртку), чтобы регрессия не повторилась.

---

### 🔴 #2 — Сезоны: `prize_pool` нигде не интегрируется

**Файл:** `apps/worker/worker/tasks/close_season.py` — worker берёт `season.prize_pool` для распределения.

**Файл:** `apps/backend/app/services/season_service.py:100-114` — `close_season`:

```python
per_member_pool = (
    season_obj.prize_pool * percentage_bp // BASIS_POINTS_TOTAL
)
share = per_member_pool // len(ranked)
```

**Файл:** `apps/backend/app/services/penalty_service.py:180` — `add_to_prize_pool` инкрементирует `habit.prize_pool`, **НЕ** `season.prize_pool`:

```python
await self._habit_repo.add_to_prize_pool(str(habit.id), amount)
```

**Файл:** `apps/backend/app/services/season_service.py:45-58` — `start_season` есть, но **не вызывается ниоткуда** (grep по всему проекту — 0 вызовов).

**Файл:** Admin API `apps/backend/app/api/admin/v1/` (`habits.py`, `uploads.py`) — **нет endpoint для создания сезона**.

#### ⚠️ Верификация: never-implemented, а не регрессия

`grep -rn "start_season" apps/ --include="*.py"` показывает **только одну строку определения** (`apps/backend/app/services/season_service.py:45`) + mypy cache. **0 вызовов в проде.**

`grep -rn "Season\|seasons\|season_id" apps/backend/app/api/ --include="*.py"` = **0 строк.** Нет admin endpoint, нет user endpoint, нет internal endpoint. Никаких воркер-тасок, кроме `close_season.run` (который только читает).

**`git show 00884e8 -- apps/backend/app/services/season_service.py`** (initial commit, 2026-07-21) — `start_season` уже существует с этой же реализацией, и `close_season` уже читает `season_obj.prize_pool`. Но в initial commit нет НИ ОДНОГО механизма переноса `Habit.prize_pool → Season.prize_pool`. Это означает: **цепочка описана в дизайне, но средняя стрелка была пропущена с самого начала.**

#### Где ошибся audit `Pravki.md §6.1`

Audit говорит:
> **Цепочка:** `Penalty.amount → Habit.prize_pool (+= FOR UPDATE) → Season.prize_pool → распределение в close_season`.

И перечисляет файлы:
> - `repositories/habit_repository.py:119-135` (`add_to_prize_pool` — атомарный инкремент с `FOR UPDATE`)
> - `services/penalty_service.py:71-156` (`apply_catch` — списание депозита + `add_to_prize_pool(habit, amount)`)
> - `services/season_service.py:60-122` (`close_season` — basis points арифметика, запись `Transaction(type=PRIZE)`)

**Ни один из этих файлов не реализует перенос `Habit.prize_pool → Season.prize_pool`.** Audit перечислил конечные точки цепочки, но не проверил передачу данных между ними. **Это структурная ошибка audit-процедуры — описание endpoint-файлов ≠ проверка работающего контракта.**

#### Эффект в проде

1. `Season.prize_pool = 0` всегда (server_default, никогда не пишется).
2. `close_season` даже если найдёт «активный» сезон (что невозможно — `Season` не создаётся), раздаст 0 ₽.
3. `close_season.run` находит 0 строк каждый день и радостно пишет `closed=0`.

**Что починить:**
- Либо сделать `Habit.prize_pool` → `Season.prize_pool` зеркалирование на момент старта сезона (snapshot), либо
- End-of-season: `for season in active: season.prize_pool = habit.prize_pool` перед распределением, либо
- В `apply_catch` / `apply_window_expired` обновлять `season.prize_pool` текущего активного сезона habit'а.
- Добавить admin endpoint `POST /admin/v1/habits/{id}/seasons` для создания Season.
- **Обновить `Pravki.md §6.1` — снять «✅ работает корректно», пока цепочка не замкнётся.**
- **Усилить audit-процедуру:** «кто вызывает эту функцию в проде?» — must-have проверка.

---

### 🔴 #3 — Cron ломает окно ловли

**Файл:** `apps/worker/worker/celery_app.py:69-75`

```python
"close_catch_window_hourly": {
    "task": "worker.tasks.close_catch_window.run_for_active_habits",
    "schedule": crontab(minute=5),  # каждый час в :05
},
```

**Файл:** `apps/worker/worker/tasks/close_catch_window.py:27` — таска skip'ает только если `is_within_checkin_window`, иначе сразу штрафует:

```python
if habit.is_within_checkin_window(now_utc):
    return {"habit_id": str(habit.id), "skipped": "window_open", "penalized": 0}
```

**По документации** (`docs/06-data-model.md` §4.4, `PenaltyConfig.CATCH_WINDOW_EXTRA_HOURS = 1`):

> Catch window = checkin window + 1 час. После `catch_window_end` cron `close_catch_window` фиксирует штрафы без улова.

**Эффект:**
- Клуб с окном чек-ина `09:00-10:00` теоретически имеет catch window до `11:00`.
- Cron в `10:05` уже видит `is_within_checkin_window(10:05) == False` → штрафует.
- У участника **5 минут** на поимку, не 60.

**Что починить:** переписать cron на per-habit-beat через `apply_async(eta=...)` от `Habit.checkin_window_end + 1h + ε` (per-habit расписание, см. SKILL «Time» секция — TZ клуба). На каждый клуб — отдельная задача в `celery_app.conf.beat_schedule` генерируется динамически при `start_season`/`create_habit`.

---

## Детали по высокоприоритетным находкам

### 🟠 #4 — Forward-detection в worker defense-in-depth не работает

**Файл:** `apps/bot/bot/handlers/checkin.py:86` — `_parse_proof` отправляет `is_forwarded: bool`:

```python
"is_forwarded": getattr(message, "forward_origin", None) is not None,
```

**Файл:** `apps/backend/app/api/v1/internal_checkins.py:48` — Payload модель:

```python
is_forwarded: bool = False
```

**Файл:** `apps/worker/worker/tasks/process_checkin.py:214-220` — worker создаёт `ProofMessage` **БЕЗ** `forward_date`:

```python
proof = ProofMessage(
    proof_type=ProofType(payload["proof_type"]),
    text=payload.get("text"),
    video_note_duration=payload.get("duration_seconds"),
    photo_sizes=1 if payload["proof_type"] == "photo" else 0,
    message_date=datetime.fromisoformat(payload["message_sent_at"]),
    # ❌ нет forward_date
)
```

**Файл:** `apps/backend/app/services/proof_validator.py:55-56` — единственная проверка `forward_date`:

```python
if message.forward_date is not None:
    raise ProofValidationError(CheckinRejectCode.FORWARDED.value)
```

**Эффект:**
- `ProofMessage.forward_date` всегда `None` → `ProofValidationError("forwarded")` **никогда** не выбрасывается.
- Реальная защита только в `enqueue_checkin` (synchronous) + bot prefilter.
- При bypass'е бота (прямой POST на `/internal/checkins/process`) — `is_forwarded=False` достаточно, чтобы пройти.

**Что починить:** в worker создавать `ProofMessage(forward_date=datetime.now() if payload.get('is_forwarded') else None)` явно, либо ввести отдельный guard на уровне worker.

---

### 🟠 #5 — `_resolve_catcher_membership_id` кросс-habit

**Файл:** `apps/backend/app/api/v1/internal_penalties.py:34-45`

```python
async def _resolve_catcher_membership_id(
    session: AsyncSession, catcher_user_id: int
) -> str | None:
    stmt = select(Membership).where(Membership.user_id == catcher_user_id)
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None
    active = [m for m in rows if m.status.value == "active"]
    return str(active[0].id) if active else None  # ❌ любой active
```

**Файл:** `apps/backend/app/services/penalty_service.py:99-100` — self-catch check:

```python
if catcher_membership_id is not None and catcher_membership_id == violator_membership_id:
    raise CannotCatchSelfError()
```

**Файл:** `apps/backend/app/repositories/suspicious_pairs_repository.py` — `lookup_flagged(catcher_membership_id, violator_membership_id)` ищет по обоим ID без фильтра по `habit_id`.

**Эффект:**
- Юзер в клубе A ловит кого-то в клубе B → `catcher_membership_id` из клуба A.
- `Penalty.catcher_membership_id` — в чужом клубе.
- `suspicious_pairs_repo.lookup_flagged(catcher_membership_id_from_A, violator_membership_id_from_B)` — ищет пару в чужом клубе, **никогда не найдёт** real-flagged пары.
- Лидерборд `catches_count` (см. `leaderboard.py:130-132`) считает penalty по `catcher_membership_id` → ловец получает «кредит» в клуб A за активность в клубе B.

Сейчас endpoint никем не вызывается → латентная дыра. Но если/когда это подключат — выстрелит.

**Что починить:** `_resolve_catcher_membership_id` должен принимать `habit_id` и фильтровать `Membership.habit_id == habit_id`. И `apply_catch` должен валидировать, что `catcher_membership.habit_id == violator.habit_id`.

---

### 🟠 #6 — Нарушение canonical priority в worker defense-in-depth

**Файл:** `apps/backend/app/services/checkin_service.py:122-241` — worker order:

1. `subscription_until` (#6)
2. `status==paused` (#7)
3. `status==left` (#8)
4. `wrong_topic` (#10)
5. `validate_proof_media` (#12)
6. `has_any_penalty_today` (#3) ← **после paused/left**
7. `joined_late` (#5)
8. `is_within_checkin_window` (#9)

**Файл:** `apps/backend/app/core/constants.py:CheckinRejectCode` docstring — канонический порядок v2:

3. caught_today
4. checkin_already_exists
5. joined_late
6. subscription_expired
7. paused
8. left
9. window_closed
10. wrong_topic
11. forwarded

**Эффект:**
- Комбо `caught_today=True AND status=paused` (штатный сценарий после `apply_catch` → `recompute_pause_status` флипает ACTIVE→PAUSED) — worker вернёт `membership_paused`, бот скажет «иди пополни».
- На проде это маскируется тем, что бот prefilter отдаёт priority v2 правильно, и worker видит combo только при bypass'е.
- Если ввести автоматизацию (drip email, push), она может реагировать на `membership_paused` вместо `caught_today`.

**Что починить:** переставить блок `has_any_penalty_today` ПЕРЕД блоком `subscription_until`/`status` в `process_checkin` (как задумано в `constants.py:151` и комментариях Шага 3).

---

## Детали по средним находкам

### 🟡 #7 — `date.today()` в `subscribe_and_join`

**Файл:** `apps/backend/app/services/membership_service.py:318`

```python
today = date.today()  # локальная TZ сервера
```

**Файл:** `apps/backend/app/services/membership_service.py:325-326`

```python
existing.subscription_until is not None
and existing.subscription_until >= today
```

**Эффект:** на сервере с TZ ≠ UTC `date.today()` может давать «вчера» или «завтра» в моменты 00:00 UTC. Docker container обычно стартует в UTC, но конфигурация не закреплена в docker-compose.

**Что починить:** `from datetime import UTC, datetime; today = datetime.now(tz=UTC).date()`.

---

### 🟡 #8 — `apply_catch` не проверяет habit catcher_membership

**Файл:** `apps/backend/app/services/penalty_service.py:97-101`

```python
violator = await self._membership_repo.get(violator_membership_id)
if catcher_membership_id is not None and catcher_membership_id == violator_membership_id:
    raise CannotCatchSelfError()
```

**Нет проверки:** `catcher_membership.habit_id == violator.habit_id`.

**Эффект:** кэтчер из habit A может формально числиться как поймавший в habit B (если caller передаст чужие ID). На текущем UI фильтруется через `get_for_user_in_habit`, но `internal_penalties.py` это не валидирует.

**Что починить:** добавить `if catcher_membership.habit_id != violator.habit_id: raise HabitMismatchError()`.

---

### 🟡 #9 — Catch rate-limit fail-mode

**Файл:** `apps/worker/worker/tasks/process_penalty.py:243-274` — прод-обёртка `run()`:

```python
redis_port = _build_production_redis_port()
if redis_port is None:
    log.error("rate_limit_unavailable", ...)
    raise RateLimitDisabledError(...)
```

**Эффект:** Redis-down → rate-limit fail-CLOSED (Celery retry). Но если Redis `INCR` падает **между** `incr` и `expire` (Redis crash mid-call), ключ остаётся без TTL → «вечный» rate-limit блокирует юзера до ручного вмешательства.

**Что починить:** использовать Lua-атомарный `INCR + EXPIRE` (как в `sse:conn:*` для лимитера коннектов), либо делать `EXPIRE` в `finally` с защитой от race.

---

### 🟡 #10 — SSE broadcasts после commit не атомарны с `apply_catch`

**Файл:** `apps/backend/app/api/v1/members.py:catch_violator:271-352`

**Эффект:** Penalty уже в БД, но если worker `publish_catch_event` упал (broker down), другие участники не видят обновление 1-2 polling раунда. Финансово безопасно.

**Что починить:** уже сделано через раздельные try/except. Альтернатива — outbox pattern в отдельном task.

---

### 🟡 #11 — `prize_pool` в Habit без изоляции по Season

**Файл:** `apps/backend/app/models/habit.py:56` — `Habit.prize_pool: int`
**Файл:** `apps/backend/app/models/season.py:28` — `Season.prize_pool: int = 0` (никогда не обновляется)

**Эффект:** штрафы за разные сезоны копятся в один `habit.prize_pool`. `close_season` читает `season.prize_pool=0`, даже если в `habit.prize_pool` уже 100 000₽ за 3 сезона.

**Что починить:** см. дыру #2 — нужна snapshot-логика.

---

### 🟡 #12 — `stale_message` window позволяет дубль

**Файл:** `apps/backend/app/services/proof_validator.py:60-62`

```python
delta = (datetime.now(tz=UTC) - message.message_date).total_seconds()
if delta < -5 or delta > max_age_seconds:  # max_age_seconds=60
    raise ProofValidationError(CheckinRejectCode.STALE_MESSAGE.value)
```

**Эффект:** в окне `[-5s, +60s]` пользователь может дважды отправить видео (например, retry Telegram доставки). Бот ответит «Принято» дважды, но `CheckinRepository.get_or_create_done` UNIQUE-индекс отработает — дубля строки не будет.

---

### 🟡 #13 — `is_within_checkin_window` не работает для окон через полночь

**Файл:** `apps/backend/app/models/habit.py:113-127`

```python
def is_within_checkin_window(self, moment_utc: datetime) -> bool:
    local = moment_utc.astimezone(self.tzinfo)
    return self.checkin_window_start <= local.time() <= self.checkin_window_end
```

**Эффект:** клуб с `start=22:00, end=06:00` (ночная планка) — `03:00` локального времени → `22:00 <= 03:00 <= 06:00` = False. Юзеру всегда «окно закрыто».

**Что починить:** используя ту же логику, что в `was_joined_after_window` (lines 129-155):

```python
if self.checkin_window_start <= self.checkin_window_end:
    return self.checkin_window_start <= local.time() <= self.checkin_window_end
return local.time() >= self.checkin_window_start or local.time() <= self.checkin_window_end
```

---

### 🟡 #14 — `refresh(violator)` не перезагружает под локом

**Файл:** `apps/backend/app/services/penalty_service.py:123` —

```python
await self._session.refresh(violator)
```

**Эффект:** `refresh()` после `lock_for_update(user)` перечитывает `violator` row, но **не делает ещё один `FOR UPDATE`**. До `flush()` другая транзакция может изменить `violator.status` через `recompute_pause_status` (если где-то он вызван вне user-lock'а). Сейчас защищено тем, что `recompute_pause_status` всегда вызывается под user-lock'ом в этой же транзакции.

**Что починить:** defensive: либо SELECT FOR UPDATE повторно на membership, либо trust + test coverage.

---

### 🟡 #15 — `effective_deposit` без race protection

**Файл:** `apps/backend/app/services/membership_service.py:361-378`

```python
effective_deposit = u.deposit_balance + deposit_amount_kopecks
if effective_deposit < habit.penalty_amount:
    raise InsufficientDepositChoiceError(...)
```

**Эффект:** под user-lock'ом race невозможен, но `effective_deposit >= penalty` с `pay_deposit=0` всё равно создаёт `dep_tx` с `amount=0`. После retry — тот же ключ → `existing_dep_row` → idempotent return. OK.

---

### 🟡 #16 — `apply_window_expired` WAIVED-ветка не пересчитывает паузу

**Файл:** `apps/backend/app/services/penalty_service.py:282-330` — для `amount <= 0` пишется WAIVED-маркер, но **`recompute_pause_status` НЕ вызывается**.

**Эффект:** юзер остаётся `status=ACTIVE` с `deposit_balance=0`. Следующий `apply_catch` (например, кто-то поймал его на следующий день) пройдёт успешно (status ACTIVE), снимет `min(panelty, 0) = 0`... нет, это другая ветка. На следующий день юзер с deposit=0 не сможет попасть в apply_catch (membership status не PAUSED, но при следующем штрафе — `min(penalty, 0) = 0` → `PenaltyAlreadyProcessedError("deposit_exhausted")`).

Но если юзер top-up'нет **после** apply_window_expired маркера, но **до** следующего штрафа — `recompute_pause_status` сделает его снова ACTIVE, и он будет жить как обычно. **Несправедливости нет в этом сценарии.** Дыра — только если закоммитить deposit topup в ту же секунду, что и apply_window_expired, что даёт race в `recompute_pause_status` через 2 разных транзакции.

**Что починить:** добавить `await self._membership_service.recompute_pause_status(violator.user_id)` в WAIVED-ветку.

---

### 🟡 #17 — `apply_catch` для `deposit=0` без WAIVED-маркера

**Файл:** `apps/backend/app/services/penalty_service.py:171-177`

```python
amount = min(habit.penalty_amount, violator_user.deposit_balance)
if amount <= 0:
    raise PenaltyAlreadyProcessedError("deposit_exhausted", code="deposit_exhausted")
```

**Эффект:** `amount=0`, маркер НЕ пишется, статус остаётся ACTIVE. Если параллельная транзакция (cron apply_window_expired для того же юзера) **уже** записала WAIVED — этот путь отвергается `existing Penalty` check'ом (penalty_service.py:161-168). Но если apply_window_expired ещё не отработал — юзер в ACTIVE+deposit=0, после topup `apply_catch` сработает на следующий день и **спишет деньги за прошлый день через WAIVED**, если такой маркер есть, либо снова спишет, если WAIVED не был записан.

**Что починить:** в `apply_catch` ветке `deposit_exhausted` тоже писать WAIVED-маркер (`reason=WAIVED_UNABLE_TO_PAY, amount=0`), идемпотентно.

---

### 🟡 #18 — `join` LEFT→ACTIVE reuse без deposit-check

**Файл:** `apps/backend/app/services/membership_service.py:58-65`

```python
if existing.status == MembershipStatus.LEFT:
    existing.status = MembershipStatus.ACTIVE
    return existing
```

**Эффект:** юзер с 0 deposit (потрачен перед leave) реактивируется без проверки депозита. Первый же штраф его переведёт в PAUSED. Юзер не понимает, «почему я опять на паузе».

**Что починить:** добавить deposit-check на ветке LEFT→ACTIVE (аналогично новой membership).

---

### 🟡 #19 — `leave` без возврата депозита

**Файл:** `apps/backend/app/services/membership_service.py:138-145`

```python
async def leave(self, *, user_id: int, habit_id: str) -> Membership:
    m = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
    ...
    m.status = MembershipStatus.LEFT
    return m
```

**Эффект:** депозит остаётся на `users.deposit_balance`. Документация заявляет «за вычетом техкомиссии ~5%» (`docs/01-concept.md:65-66`, `4_finansovaya_mehanika:65`), но **возврата нет вообще** — деньги просто остаются глобально. Юзер, ушедший из клуба без других клубов, не может их вывести.

**Что починить:** нет `POST /leave/refund` endpoint. Если MVP — оставить как есть, но **поправить документацию** (убрать обещание возврата).

---

## Низкие находки (для полноты)

### 🟢 #20 — `current_user_db` upsert без согласия

**Файл:** `apps/backend/app/api/v1/users.py:33-56`

```python
async def current_user_db(...):
    repo = UserRepository(session)
    await repo.upsert(id=user.id, first_name=..., username=...)
    await session.commit()
```

**Эффект:** любой GET-запрос к `/api/v1/me/wallet`, `/api/v1/balance`, `/api/v1/leaderboard`, `/api/v1/habits` (marketplace) создаёт запись в `users` **до** `/start` бота и **до** `accepted_offer_at`. ФЗ-152: ПДн записывается без явного согласия.

**Что починить:** создавать user запись только при первом `/start` (через бота) или явном `accept_offer` (в MiniApp). `current_user_db` должен возвращать 404 для не-existent user, а не auto-create.

---

### 🟢 #21 — `bonus_points_updated_at` NULL → нет сгорания

**Файл:** `apps/worker/worker/tasks/expire_bonus_points.py:22-26`

```python
.where(
    User.bonus_points > 0,
    User.bonus_points_updated_at.is_not(None),
    User.bonus_points_updated_at < cutoff,
)
```

**Эффект:** если `bonus_points_updated_at` остался NULL (юзер получил бонус до миграции или `bonus_service` не обновил timestamp — см. #1), сгорания не происходит.

**Что починить:** бэкфилл `bonus_points_updated_at = now() WHERE bonus_points > 0 AND bonus_points_updated_at IS NULL` в миграции, либо изменить условие.

---

### 🟢 #22 — `suspicious_pairs.evaluate_after_catch` мёртвый код

**Файл:** `apps/backend/app/services/suspicious_pairs_service.py:32-82`

Grep по всему проекту — **0 вызовов** `evaluate_after_catch`. `SUSPICIOUS_ASYMMETRY_THRESHOLD=3` недостижим.

**Что починить:** вызвать из `process_penalty.run` после `apply_catch` успеха.

---

### 🟢 #23 — `IntegrityError` в catch race теряет SSE broadcasts

**Файл:** `apps/worker/worker/tasks/process_penalty.py:210-213`

```python
except IntegrityError as exc:
    await session.rollback()
    log.info("worker_penalty_integrity", extra={"err": str(exc)})
    return {"ok": True, "duplicate": True}
```

**Эффект:** при гонке двух кэтчеров один получает `IntegrityError` (UNIQUE на `(membership_id, date, reason)`) — penalty не создан, broadcasts не отправлены. UI этого кэтчера не подтверждает catch.

**Что починить:** отправлять broadcasts даже на `IntegrityError`, если penalty_id удалось получить до rollback.

---

### 🟢 #24 — Leaderboard не включает archived клубы

**Файл:** `apps/backend/app/repositories/habit_repository.py:57-64` — `list_active()` фильтрует `is_active=True AND archived_at IS NULL`.

**Эффект:** после `archive()` клуб исчезает из leaderboard. Дизайн-решение, не дыра.

---

### 🟢 #25 — `catch_rate_limiter` INCR persistent при error

**Файл:** `apps/backend/app/services/catch_rate_limiter.py` (см. `penalty_service.py:84-87`)

```python
count = await self._redis.incr_catch(catcher_user_id)
if count > parse_rate_limit_spec(PenaltyConfig.RATE_LIMIT_CATCH)[0]:
    raise TooManyCatchAttemptsError()
```

**Эффект:** если после `incr_catch` бросилось исключение (например, `apply_catch` → `HabitNotFoundError`), ключ уже инкрементнут. Через 10с истечёт, но в моменте пользователь получает ложные срабатывания.

---

### 🟢 #26 — `stale_message` 60s window ловит медленный Telegram

**Файл:** `apps/backend/app/services/proof_validator.py:60-62`

**Эффект:** медленный мобильный Telegram (3G, перегрузка) может дать `delta > 60` → ложный `stale_message` без понятного UI-объяснения.

---

### 🟢 #27 — SSE idempotency TTL 24h

**Файл:** `docs/06-data-model.md` §11 — `sse_published:checkin:{m}:{d}` TTL=86400

**Эффект:** при ручном retry через 25ч дубль SSE-frame. Маловероятно.

---

### 🟢 #28 — `SeasonStats` не инкрементируется нигде

**Файл:** `apps/backend/app/models/season.py:44-62`

`streak_days`, `total_penalties_caught`, `total_penalties_received` — все 0. Leaderboard считает из `Checkin`/`Penalty` напрямую. `SeasonStats` — orphan table.

---

## Известные технические долги (tech-debt backlog, не блокирует прод)

> **Snapshot 2026-08-19.** Зафиксировано по итогам аудита забытой фичи `wip-other-task`
> (история коммитов `cf4db84` → `c7f8d87`, 2026-07-22). Фича в итоге реализована
> через `c7f8d87 feat(admin): archive→permanent-delete + admin Mini App hardening`
> и задеплоена. Ниже — 4 известных недочёта в production-коде, **не дыры**,
> **не срочно**, рекомендуется вынести в отдельный sprint когда появится время.

| # | Недочёт | Severity | Файл(ы) | Что делать |
|---|---|---|---|---|
| TD-1 | Бизнес-логика в роуте `list_available_chats` (~200 строк: reconciliation чатов, обработка миграции в супергруппу, удаление из Redis) — нарушает layered architecture (правило: сложная логика в `HabitService`, роут — тонкая обёртка) | 🟡 Medium | `apps/backend/app/api/admin/v1/habits.py:list_available_chats` | Вынести в `HabitService` (новый метод `list_available_chats_with_reconcile`), роут оставить как тонкую обёртку. |
| TD-2 | Нет rate-limit на вызовы Telegram Bot API через `_verify_chats_via_telegram` (использует `asyncio.gather` × N чатов, лимит Telegram = **30 req/sec глобально**). Существующий `RATE_LIMIT_API_V1=60/60s` ограничивает только наш `/api/v1/*`, не Bot API | 🟡 Medium | `apps/backend/app/api/admin/v1/habits.py:_verify_chats_via_telegram`, `_get_bot_id` | Добавить in-process token bucket / asyncio.Lock на вызовы `getChatMember`+`getChat` в пределах одного запроса. Кэш результата `getChatMember` на 5-10 секунд (на горячем пути в админку). |
| TD-3 | Прямое обращение к приватным полям сервиса из роута: `service._habit_repo.X` (см. `list_available_chats`, `permanent_delete_habit`) — нарушает инкапсуляцию, прямой доступ к `_session` для `commit()` | 🟡 Medium | `apps/backend/app/api/admin/v1/habits.py` (несколько мест) | Пробросить через публичный API `HabitService` — добавить методы `unbind_chat(habit_id)`, `get_chats_for_reconcile()`. `commit()` оставить только в admin endpoint'ах (по исключению из AGENTS.md). |
| TD-4 | Нет тестов для `_verify_chats_via_telegram` (Bot API вызовы + reconcile) и `bot/handlers/chat_member.py` (my_chat_member обработка). Есть только общие admin endpoint тесты в `test_admin_habits_api.py` | 🟢 Low | `apps/backend/tests/` (добавить `test_chat_preview.py`), `apps/bot/tests/` (добавить `test_chat_member.py`) | Покрыть: (1) Telegram API ответы 200 / 400 / chat_not_found / migrated_to_chat_id / bot_kicked; (2) бот-хендлер `my_chat_member` для переходов `IS_NOT_MEMBER → IS_MEMBER` и `IS_MEMBER → IS_NOT_MEMBER`. |

**Почему не блокер:** на проде сейчас 3 клуба, админ заходит в панель редко, реального спама по Bot API не наблюдается. При росте до 50+ клубов и активном админе — **TD-2 начнёт стрелять первым** (429 от Telegram → деградация UX админки).

**Не делать правки по собственной инициативе** — это задача для отдельного sprint'а, не блокирует текущие релизы.

---

## Race-conditions, которые защищены, но fragile

- **`apply_catch` user-lock + refresh** (penalty_service.py:105-123) — защита от `membership.status` race. Защита работает, но **основана на дисциплине вызова `recompute_pause_status` под тем же lock'ом**. Если будущий рефакторинг разорвёт эту связь — race вернётся.
- **`subscribe_and_join` idempotency** через `dep_key` UNIQUE — OK, но **двойное списание price_month НЕ блокируется** между `subscribe_and_join` и `topup_deposit` если не на том же user-lock. Сейчас оба пути идут через `lock_for_update(user_id)` → сериализуются.
- **`process_checkin` Celery retry с `max_retries=3`** — при 3 ретраях и `IntegrityError` будет 3 дубля задачи, но `_process` возвращает `duplicate=True` idempotent. OK.

---

## Что не нашёл (что хорошо защищено)

- ✅ `user_id` берётся ТОЛЬКО из `request.state.telegram_user` — везде через `TelegramUserDbDep`.
- ✅ Деньги — `int` копейки, нигде нет `float` / `Decimal` (грепнул).
- ✅ UNIQUE-индексы на `(membership_id, date)` проверяются в `IntegrityError` handler'ах.
- ✅ Penalty.idempotency_key уникальный, но фактически **НЕ используется** в `apply_catch` (видел в `payke_service.py:107-168` упоминание, но в `apply_catch` идёт проверка через existing Penalty, не idempotency_key). Race ловится на UNIQUE + existing-check + `PenaltyAlreadyProcessedError`.
- ✅ `bonus_applied` flag предотвращает двойное начисление (но из-за #1 это не работает).
- ✅ `lock_for_update` на user покрывает все денежные операции.
- ✅ WSGI/ASGI middleware-упорядочивание: CORS preflight → Auth → RateLimit → RequestContext.
- ✅ Forwarded check в bot prefilter + `enqueue_checkin` (хоть в worker defense-in-depth сломан, см. #4).
- ✅ `HabitRepository.add_to_prize_pool` делает `SELECT FOR UPDATE` на Habit — атомарный инкремент `prize_pool`.
- ✅ `recompute_pause_status` под user-lock'ом — корректная синхронизация статусов всех клубов юзера.

---

## Сводный чек-лист для принятия решений

| Дыра | Severity | Когда стреляет | Сложность фикса | Блокирует прод? |
|---|---|---|---|---|
| #1 apply_catch_bonus не вызывается | 🔴 Крит | Каждый catch | 2 строки + регистрация в `_TASK_NAMES` | Да |
| #2 Сезоны — мёртвый код | 🔴 Крит | Первый сезон | 5+ файлов, admin endpoint + snapshot логика | Нет (ещё не нужно) |
| #3 Cron ломает окно ловли | 🔴 Крит | Каждый день после окна | Средне — per-habit-beat | Да |
| #4 Forward в worker defense-in-depth | 🟠 Выс | bypassed bot | 1-2 строки | Нет |
| #5 _resolve_catcher_membership_id кросс-habit | 🟠 Выс | если endpoint задействуют | 1 фильтр | Нет |
| #6 Приоритет проверок в worker | 🟠 Выс | combo caught_today+paused | Reorder 10 строк | Нет |
| #7 date.today() vs UTC | 🟡 Ср | сервер в не-UTC TZ | 1 строка | Нет |
| #8 apply_catch habit-mismatch | 🟡 Ср | если endpoint задействуют | 1 if | Нет |
| #9 catch_rate_limiter fail-mode | 🟡 Ср | Redis crash | Lua-атомарный INCR | Нет |
| #10 SSE broadcasts после commit | 🟡 Ср | broker down | outbox pattern | Нет |
| #11 prize_pool без изоляции | 🟡 Ср | при включении сезонов | Связано с #2 | Нет |
| #12 stale_message дубль | 🟡 Ср | retry Telegram | UI hint | Нет |
| #13 Окна через полночь | 🟡 Ср | ночные клубы | 3 строки | Нет (фича не запущена) |
| #14 refresh(violator) под локом | 🟡 Ср | edge-case | defensive SELECT FOR UPDATE | Нет |
| #15 effective_deposit без race | 🟡 Ср | крайне редкий | covered by user-lock | Нет |
| #16 WAIVED-ветка без recompute | 🟡 Ср | deposit=0 ACTIVE | 1 строка recompute | Нет |
| #17 apply_catch deposit=0 без WAIVED | 🟡 Ср | deposit=0 ACTIVE | 5 строк | Нет |
| #18 reuse LEFT без deposit-check | 🟡 Ср | возврат в клуб | 1 if блок | Нет |
| #19 leave без возврата депозита | 🟡 Ср | UX, юзер уходит | отдельная задача возврата | Нет |
| #20 current_user_db ФЗ-152 | 🟢 Низ | каждый посетитель | Миграция + изменения в auth | Нет |
| #21 bonus_points_updated_at NULL | 🟢 Низ | legacy users | миграция бэкфилл | Нет |
| #22 suspicious_pairs evaluate_after_catch | 🟢 Низ | антифрод не работает | 1 строка в process_penalty | Нет |
| #23 IntegrityError теряет SSE | 🟢 Низ | race двух кэтчеров | broadcasts в except | Нет |
| #24 archived клубы в leaderboard | 🟢 Низ | дизайн | нет | Нет |
| #25 catch_rate INCR persistent | 🟢 Низ | error path | уточнить redis-клиент | Нет |
| #26 stale_message 60s | 🟢 Низ | медленный Telegram | UI hint | Нет |
| #27 SSE TTL 24h | 🟢 Низ | retry через 25ч | Увеличить TTL | Нет |
| #28 SeasonStats не пишется | 🟢 Низ | пока | связано с #2 | Нет |

---

## Рекомендуемый порядок фиксов

### Sprint 1 — критические для прода
1. **#1** `apply_catch_bonus` — 2 строки, восстанавливает обещанную ловцам механику.
2. **#3** cron ломает catch window — переписать на per-habit-beat.
3. **#17** `apply_catch` deposit=0 пишет WAIVED-маркер — закрывает «штраф за прошлый день» после topup.
4. **#16** `apply_window_expired` WAIVED-ветка вызывает `recompute_pause_status` — закрывает тот же класс багов для ACTIVE+deposit=0.

### Sprint 2 — антифрод hardening
5. **#4** forward в worker defense-in-depth.
6. **#6** canonical priority в worker.
7. **#22** `evaluate_after_catch` триггер из process_penalty.
8. **#5** `_resolve_catcher_membership_id` фильтр по habit_id.
9. **#8** `apply_catch` habit-mismatch.

### Sprint 3 — UX/ФЗ-152 polish
10. **#13** окна через полночь.
11. **#7** `date.today()` → UTC.
12. **#20** ФЗ-152 — user только после `/start` или `accept_offer`.
13. **#18** LEFT→ACTIVE reuse с deposit-check.
14. **#19** leave + refund (или документировать отсутствие).

### Sprint 4 — seasons enable (нужен product decision)
15. **#2** + **#11** + **#28** — связаны. Нужен admin endpoint, snapshot-логика, snapshot apply в apply_catch/apply_window_expired.

---

## Связанные доки (для следующего агента)

- `Pravki-deposit-sse.md` — депозит на user, idempotency, SSE.
- `Pravki-subscribe-and-join.md` — subscribe_and_join, smart renew.
- `Pravki-subscription-2026-08-17.md` — canonical #6 subscription_expired.
- `Pravki-no-deposit-waived-marker.md` — WAIVED-маркер для PAUSED.
- `Pravki.md` — общий контекст серий фиксов.
- `docs/04-code-standards.md` §7.1 — pre-filter pattern.
- `docs/06-data-model.md` — модель данных, антифрод, идемпотентность.
- `docs/09-prod-readiness.md` §1.1 — известные ограничения прода.

**Изменений в коде не делал. Документ только для разведки.**

---

## 🗣️ Простым языком — что за задача в каждом пункте

> TL;DR для быстрого понимания. Каждая дыра в формате:
> **Ошибка → Что происходит → На что влияет → Что даст фикс**.

### 🔴 Критические (Sprint 1 — деньги пользователей прямо сейчас)

**#1 Ловец не получает баллы**
- **Ошибка:** обещали «+1 бонусный балл за каждого пойманного», но кода, который это начисляет, в проде нет. Функция написана, тесты зелёные, но никто её не вызывает.
- **Что происходит:** юзер поймал 10 человек → его счёт bonus_points = 0. Лидерборд «Охотники» пустой.
- **На что влияет:** на честность системы. Юзеры, которые активно ловят, видят что их «не вознаграждают» — теряют мотивацию.
- **Что даст фикс:** ловцы реально получают баллы, лидерборд работает, мотивация восстанавливается. +1 строка + регистрация в `_TASK_NAMES` + e2e-тест через broker (чтобы регрессия не повторилась).

**#2 Призовой фонд не распределяется**
- **Ошибка:** дизайн говорит «штрафы копятся в призовом фонде, в конце сезона раздаются победителям». По факту — штрафы копятся, но в конце сезона раздаётся 0₽, потому что средняя стрелка цепочки никогда не была реализована.
- **Что происходит:** клуб накопил 50 000₽ штрафов за сезон → `close_season` раздаёт **0₽** всем победителям. Деньги просто остаются в `Habit.prize_pool`.
- **На что влияет:** на сезонную экономику. Победители не получают призы — главная мотивация к честным чек-инам исчезает.
- **Что даст фикс:** реальные призы в конце сезона, сезонная механика работает как обещано. Требует admin endpoint + snapshot-логику (Sprint 4, не блокер).

**#3 Окно ловли всего 5 минут вместо часа**
- **Ошибка:** документация обещает «после окна чек-ина у тебя 1 час чтобы поймать нарушителя». Реальность — у тебя 5 минут, потому что cron штрафует через 5 минут после закрытия окна.
- **Что происходит:** окно чек-ина закрылось в 10:00 → cron в 10:05 уже списывает штрафы всем, кто не отметился. Все, кто хотел поймать кого-то в 10:30 — опоздали.
- **На что влияет:** на социальный контроль. Главная фича клубов («поймать прогульщика») почти не работает.
- **Что даст фикс:** реальный час на поимку, социальный контроль включается как обещано. Per-habit-beat через `apply_async(eta=...)`.

### 🟠 Высокие (Sprint 2 — антифрод hardening)

**#4 Пересланные кружки можно протащить мимо бота**
- **Ошибка:** основная защита от пересланных видео — в боте (prefilter). Бэкенд тоже проверяет, но в worker defense-in-depth (третий уровень защиты) проверка сломана.
- **Что происходит:** обычно всё ок — бот ловит первым. Но если кто-то получит service-token бота и пошлёт напрямую — пройдёт.
- **На что влияет:** на антифрод. Сейчас низкий риск (service-token в секрете), но это «дыра на чёрный день».
- **Что даст фикс:** трёхуровневая защита работает, нельзя обойти даже с токеном. 1-2 строки.

**#5 Ловец может получить «кредит» в чужом клубе**
- **Ошибка:** функция, которая определяет «каким ловом membership ловца числится», берёт любой активный membership юзера — даже из другого клуба.
- **Что происходит:** (потенциально) юзер ловит кого-то в клубе A → получает leaderboard-очки в клубе B.
- **На что влияет:** на честность leaderboard'а. Сейчас endpoint никто не зовёт, но при починке pay-флоу выстрелит.
- **Что даст фикс:** ловец всегда числится в правильном клубе, leaderboard честный. 1 фильтр.

**#6 Юзер видит «пополни депозит» вместо «тебя поймали»**
- **Ошибка:** после поимки membership автоматически становится PAUSED (депозит обнулился). Если юзер после этого попытается чек-иниться, worker defense-in-depth скажет «пополни депозит», хотя ему важнее «тебя поймали».
- **Что происходит:** (теоретически, при bypass бота) юзер не понимает, что с ним случилось.
- **На что влияет:** на UX и понимание событий. На проде маскируется тем, что бот prefilter работает правильно.
- **Что даст фикс:** правильная диагностика, юзер понимает что произошло. Reorder 10 строк.

### 🟡 Средние (Sprint 3-4 — UX/edge cases)

**#7 Граница суток может сбиться на не-UTC сервере**
- **Ошибка:** подписка продлевается по `date.today()` — локальной дате сервера. Если сервер не в UTC — граница суток сдвигается.
- **Что происходит:** сейчас Docker по умолчанию UTC — не стреляет. Но fragile.
- **На что влияет:** на точность подписки. Может списать день «на день раньше».
- **Что даст фикс:** однозначная граница суток в UTC. 1 строка.

**#8 Ловец из клуба A не должен ловить в клубе B**
- **Ошибка:** (см. #5, но другая сторона) backend не проверяет что `catcher_membership.habit_id == violator.habit_id`.
- **Что происходит:** UI фильтрует, но API не защищён.
- **На что влияет:** на API-контракт. Сейчас не стреляет.
- **Что даст фикс:** API-контракт безопасный. 1 if.

**#9 Rate-limit может сломаться при Redis-сбое**
- **Ошибка:** если Redis падает на полпути `INCR`/`EXPIRE` — лимит может «застрять» и блокировать юзера вечно.
- **Что происходит:** крайне редко, но возможно.
- **На что влияет:** на антифрод. Сейчас fail-CLOSED с retry, но в edge-case не идеально.
- **Что даст фикс:** атомарный Lua-скрипт, невозможно рассогласование.

**#10 SSE-события о поимке могут потеряться**
- **Ошибка:** после успешного штрафа backend пытается отправить SSE-событие другим участникам. Если broker в момент отправки упал — событие теряется.
- **Что происходит:** деньги уже списаны (штраф в БД), но UI других участников не обновится 1-2 polling раунда.
- **На что влияет:** только на UX. Финансово безопасно.
- **Что даст фикс:** outbox pattern, но это сложно. Можно отложить.

**#11 Деньги клубов смешиваются между сезонами**
- **Ошибка:** (см. #2) `Habit.prize_pool` общий для всех сезонов, `Season.prize_pool` пустой.
- **Что происходит:** при починке #2 нужно ещё разделить фонд по сезонам.
- **На что влияет:** на корректность призов. Сейчас не стреляет.
- **Что даст фикс:** деньги сезона не утекают в другие сезоны. Часть Sprint 4.

**#12 Двойная отправка одного видео в узком окне**
- **Ошибка:** бот отвечает «Принято» дубля, но БД защищена UNIQUE-индексом. Дубль не записывается, но бот говорит «Принято» дважды.
- **Что происходит:** юзер видит «Принято» дважды, но реальный чек-ин один.
- **На что влияет:** на UX. Финансово безопасно.
- **Что даст фикс:** UI-hint «уже принято».

**#13 Ночные клубы не работают**
- **Ошибка:** клуб «Ночной режим» с окном 22:00–06:00 — в 03:00 показывает «окно закрыто».
- **Что происходит:** любой ночной клуб не работает.
- **На что влияет:** на ночные привычки (планка в 3 ночи — ок, но клуб отвергает).
- **Что даст фикс:** ночные клубы работают. 3 строки.

**#14 Race в refresh(violator)**
- **Ошибка:** после `lock_for_update(user)` обновляем `violator` через `refresh()` — но `refresh()` не делает повторный FOR UPDATE.
- **Что происходит:** сейчас защищено тем, что `recompute_pause_status` вызывается под тем же lock'ом. Но fragile — если будущий рефакторинг сломает дисциплину, race вернётся.
- **На что влияет:** на защиту от race condition. Не стреляет, но fragile.
- **Что даст фикс:** defensive SELECT FOR UPDATE.

**#15 effective_deposit без race**
- **Ошибка:** в smart renew считаем «депозит + новый платёж» — теоретически возможна гонка с конкурентным списанием.
- **Что происходит:** сейчас защищено user-lock'ом.
- **На что влияет:** на edge-case. Не стреляет.
- **Что даст фикс:** ничего нового, user-lock уже защищает.

**#16 Юзер с 0₽ остаётся ACTIVE после маркера**
- **Ошибка:** юзер с 0₽ депозита в ACTIVE → cron пишет WAIVED-маркер → но НЕ пересчитывает статус. Юзер остаётся ACTIVE.
- **Что происходит:** после topup юзер сразу становится ACTIVE (recompute_pause_status) — а если штраф был «вчера», новый apply_catch может списать деньги за «вчера».
- **На что влияет:** на справедливость. Юзер не должен платить за день, который он «уже прошёл» с 0₽.
- **Что даст фикс:** справедливое списание. 1 строка `recompute_pause_status`.

**#17 Штраф списывается после topup за «старый» день**
- **Ошибка:** `apply_catch` для deposit=0 выбрасывает ошибку, но НЕ пишет WAIVED-маркер. Если параллельно cron не записал — после topup catch снимет деньги за прошлый день.
- **Что происходит:** несправедливая потеря денег (юзер думал «проехал тот день», а через день с него списали).
- **На что влияет:** на доверие к системе.
- **Что даст фикс:** справедливое списание, маркер пишется в обоих путях. 5 строк.

**#18 Возврат в клуб без проверки депозита**
- **Ошибка:** юзер с 0₽ может вернуться в клуб (LEFT→ACTIVE reuse) без проверки депозита. Первый же штраф переводит в PAUSED, юзер не понимает почему.
- **Что происходит:** UX-ловушка — «вернулся, опять на паузе».
- **На что влияет:** на UX.
- **Что даст фикс:** «возврат заблокирован — пополни депозит сначала». 1 if блок.

**#19 Уход из клуба — деньги «висят»**
- **Ошибка:** юзер уходит из клуба, депозит остаётся на общем счёте. Документация обещала возврат за вычетом 5% — возврата нет.
- **Что происходит:** деньги не потеряны (можно использовать в другом клубе), но UX — «куда делись мои деньги?».
- **На что влияет:** на UX и доверие.
- **Что даст фикс:** либо вернуть, либо убрать обещание из доков.

### 🟢 Низкие (косметика)

**#20 Первый визит создаёт запись без согласия**
- **Ошибка:** GET `/api/v1/me/wallet` создаёт запись в `users` **до** того, как юзер согласился с правилами (ФЗ-152).
- **Что влияет:** на ФЗ-152 — ПДн пишутся без явного согласия.
- **Фикс:** создавать user только после `/start` или `accept_offer`.

**#21 Сгорание бонусов не работает для NULL-поля**
- **Ошибка:** если `bonus_points_updated_at` NULL (юзер до миграции) — сгорание не срабатывает.
- **Что влияет:** на сгорание бонусов. Не стреляет (#1 не работает).
- **Фикс:** бэкфилл миграцией.

**#22 Антифрод-эвристика мертва**
- **Ошибка:** функция `suspicious_pairs.evaluate_after_catch` определена, но никогда не вызывается. Порог SUSPICIOUS_ASYMMETRY_THRESHOLD=3 недостижим.
- **Что влияет:** на антифрод.
- **Фикс:** вызвать из `process_penalty`.

**#23 Дубликат при гонке двух кэтчеров теряет SSE**
- **Ошибка:** если два юзера одновременно ловят одну жертву — UNIQUE-индекс рвёт одну транзакцию, SSE не отправляется.
- **Что влияет:** на UX одного из кэтчеров.
- **Фикс:** отправлять SSE даже при IntegrityError.

**#24-#28** — архитектурные мелочи, дизайн-решения, мёртвый код. Не блокируют прод. См. таблицу в сводном чек-листе выше.

---

## 🎯 Что даст полная реализация всех спринтов

| Спринт | Что получаем |
|---|---|
| **Sprint 1** (3 крита: #1, #3, #17 + #16) | Бонусы ловцам работают, окно ловли реально час, штрафы справедливые. **Деньги пользователей больше не «повисают в воздухе».** |
| **Sprint 2** (3 антифрод: #4, #6, #22 + #5, #8) | Трёхуровневая защита от пересыла, правильная диагностика событий, антифрод-эвристика работает. **Систему нельзя обмануть на edge-cases.** |
| **Sprint 3** (UX/ФЗ-152: #13, #7, #20 + #18, #19) | Ночные клубы работают, время UTC-однозначное, ФЗ-152 соблюдён, справедливые UX-флоу. |
| **Sprint 4** (Сезоны MVP: #2, #11, #28) | Сезонная экономика работает, реальные призы в конце сезона. |

**Главный результат:** MVP становится production-ready для запуска на реальных пользователях (сейчас 10 тестовых юзеров, 0 транзакций — стрелять нечему, но при первом сезоне всё поломается).

---

## 📋 Сигнал для следующего шага

**Статус по противоречиям** (на 2026-08-18):

| Противоречие | Разрешение | Действие |
|---|---|---|
| #1 apply_catch_bonus | ✅ Моя разведка **верна**. Wiring отсутствует с самого начала (commit `8fc2b71` от 2026-07-21). `Pravki.md §6.2` audit ошибочно пометил «✅ работает корректно». | Sprint 1 (фикс #1) — добавить `send_task` в `process_penalty.run` + регистрация в `_TASK_NAMES`. **Также e2e-тест через broker**, чтобы регрессия не повторилась. |
| #2 Season.prize_pool | ✅ Моя разведка **верна**. Перенос `Habit.prize_pool → Season.prize_pool` никогда не был реализован. `Pravki.md §6.1` audit перечислил endpoint-файлы без проверки передачи данных. | Sprint 4 — доделать MVP-фичу (admin endpoint + snapshot). Также **обновить `Pravki.md §6.1`** — снять «✅», иначе следующий агент будет введён в заблуждение. |

**Статус по остальным критическим** (без противоречий):
- ✅ #3 (cron ломает catch window) — подтверждено, идёт в Sprint 1.
- ✅ #4 (forward в worker defense-in-depth) — подтверждено, идёт в Sprint 2.
- ✅ #6 (canonical priority в worker) — подтверждено, идёт в Sprint 2.
- ✅ #13 (окна через полночь) — подтверждено, идёт в Sprint 3.

**Audit-процедуру нужно ужесточить** — фиксировать **«кто зовёт эту функцию в проде?»**, а не только «существует ли файл с этой логикой». Добавить в `AGENTS.md §11 DoD» галочку «проверен production call chain (не только unit-тесты)».

**Изменений в коде не делал. Документ обновлён с результатами верификации.**