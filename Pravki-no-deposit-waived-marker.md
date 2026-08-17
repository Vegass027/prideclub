# Pravki-no-deposit-waived-marker

> **Snapshot 2026-08-17.** Закрытая финансовая дыра «окно чек-ина закрылось,
> deposit=0, юзер топит депозит, другой участник успевает поймать его за уже
> прошедший день, деньги списываются повторно». 4 атомарных коммита
> (`3796531` + `5bfeec7` + `241115f` + `4dc2b08`) на ветке
> `feature/qa-batch-2026-08-14`. Никаких миграций БД — `penalties.reason`
> это `String(64)` VARCHAR без CHECK constraint, новое значение enum
> принимается без изменений схемы.

## Контекст (юзер-репорт)

«Окно чек-ина закрыто, депозит пуст, юзер тут же пополняет и думает
"сейчас отмечусь завтра". Другой участник заходит, видит жертву в
списке "можно поймать", ловит её — деньги списываются, у жертвы депозит
снова пуст. Человек в гневе говорит "развод" и уходит».

## Корневая причина

`PenaltyService.apply_window_expired` при `deposit == 0` **возвращал `None` ДО
любых DB-write** (`apps/backend/app/services/penalty_service.py:254-258` —
pre-#2 код). В БД не оставалось **никакого следа** — ни `Penalty`, ни
`Checkin`, ни `Transaction`. День был полностью «непомеченным» в БД.

Следствие:
- `can_catch` в `GET /habits/{id}/members` опирался на
  `penalty_repo.ids_with_any_penalty_today(membership_ids, club_date)` —
  если штрафа за день нет, `can_catch=True`.
- `apply_catch` проверял идемпотентность только по `reason=CAUGHT` — НЕ
  ловил отсутствующий `WINDOW_CLOSED_NO_CATCH` (потому что reason другой).
- После topup депозита `MembershipService.recompute_pause_status`
  переключал membership в `ACTIVE` (`apps/backend/app/services/membership_service.py:147-206`) —
  повторный catch становился технически валиден.

## Решение

| Компонент | До | После |
|---|---|---|
| `apply_window_expired` при `deposit == 0` | `return None` (silent) | Создаёт `Penalty(reason=WAIVED_UNABLE_TO_PAY, amount=0)` + `flush()` + `return None`. **Checkin/Transaction/recompute НЕ трогаются.** |
| `apply_catch` idempotency | `WHERE reason == CAUGHT` | `WHERE (membership_id, date)` — любая Penalty за день блокирует catch. Единый код `penalty_already_processed` для всех reason'ов. |
| `close_catch_window._close_for_habit` | Дублирующий вызов `apply_window_expired` после `continue` (мёртвый код) | Удалён (10 строк). |
| `PenaltyReason` enum (Python) | `CAUGHT`, `WINDOW_CLOSED_NO_CATCH` | Добавлен `WAIVED_UNABLE_TO_PAY`. **Не Postgres ENUM**, а `StrEnum` — SQLAlchemy coercion пишет строку в VARCHAR. |

### Контракт маркера `WAIVED_UNABLE_TO_PAY`

```python
Penalty(
    id=str(uuid4()),
    membership_id=violator_membership_id,
    catcher_membership_id=None,
    amount=0,
    fund_share=0,
    catcher_bonus_points=0,
    reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
    date=club_date,
    bonus_applied=False,
)
```

- `amount=0`, `fund_share=0` — никакого финансового события.
- `catcher_membership_id=None` — никто не ловил.
- `catcher_bonus_points=0`, `bonus_applied=False` — бонус не начисляется.

### Контракт `apply_catch` (коммит `241115f`)

```python
existing = await self._session.execute(
    Penalty.__table__.select().where(
        Penalty.membership_id == violator_membership_id,
        Penalty.date == club_date,
    )
)
if existing.first() is not None:
    raise PenaltyAlreadyProcessedError()
# default code: "penalty_already_processed"
```

Фильтр `reason == CAUGHT` убран. Каждый клуб-день независим.

## Закрытые дыры

| # | Дыра | Как закрыта |
|---|---|---|
| 1 | **PRIMARY** — после `WAIVED_UNABLE_TO_PAY` за день catch списывал деньги повторно | `apply_catch` видит `WAIVED` в existing-check → отвергает |
| 2 | **BONUS** — прямой `POST /catch` поверх `WINDOW_CLOSED_NO_CATCH` (UNIQUE `uq_penalty_per_day_reason` пропускал из-за reason) | `apply_catch` existing-check без фильтра по reason → отвергает |
| 3 | **REGRESSION** — повторный catch поверх `CAUGHT` | Защищён reason-фильтром раньше, теперь общим условием |
| 4 | **DEAD CODE** — `close_catch_window` дублирующий вызов `apply_window_expired` (unreachable) | Удалён (10 строк) |

## Никаких изменений в схеме БД

- `penalties.reason` — `String(64)` VARCHAR (см. `apps/backend/app/models/penalty.py:37-39`).
- Никакая миграция Alembic не нужна. Нет `ALTER TYPE penalty_reason ADD VALUE` —
  Postgres ENUM `penalty_reason` не существует (в схеме только `checkin_status`).
- Python `StrEnum` `PenaltyReason` коэрсится в строку через SQLAlchemy
  (`Mapped[PenaltyReason]` → `String(64)`), значение `"waived_unable_to_pay"`
  принимается без каких-либо изменений БД.

## Тестовая матрица (6 новых тестов, всё зелёное)

| Тест | Файл | Контракт |
|---|---|---|
| `test_apply_window_expired_writes_waived_marker_when_deposit_zero` | `tests/test_penalty_service.py` | `deposit=0` → создаётся `Penalty(reason=WAIVED_UNABLE_TO_PAY, amount=0)`, `result is None`, нет `Transaction`, баланс не изменился, `prize_pool` не инкремент |
| `test_apply_window_expired_idempotent_after_waived_marker` | то же | Два вызова `apply_window_expired` для одного `(membership, date)` при `deposit=0` → одна `Penalty` в session (existing-check ловит дубль) |
| `test_apply_catch_rejected_when_waived_marker_exists` | то же | `WAIVED` за сегодня + `apply_catch(today)` → `PenaltyAlreadyProcessedError(code="penalty_already_processed")`, нет новых Penalty |
| `test_apply_catch_rejected_when_window_closed_penalty_exists` | то же | `WINDOW_CLOSED_NO_CATCH` за сегодня + `apply_catch(today)` → reject (бонус — закрытие дыры #2) |
| `test_apply_catch_rejected_when_existing_caught_penalty` | то же | `CAUGHT` за сегодня + `apply_catch(today)` → reject (регрессия) |
| `test_apply_catch_succeeds_for_other_date_when_waived_marker_for_previous_day` | то же | `WAIVED` за вчера + `apply_catch(сегодня)` → catch **успешен** (новый `CAUGHT` за сегодня) |

### Результаты прогона

| Слой | Baseline (pre-#1) | После #4 (HEAD `4dc2b08`) | После commit A (HEAD `fffa7c4`, финал PR) |
|---|---|---|---|
| `apps/backend/tests/test_penalty_service.py` | 6 passed | **12 passed** (+6) | **17 passed** (+5 новых для `mark_waived_unable_to_pay`) |
| `apps/backend/tests` (полный) | 393 passed, 16 failed | **399 passed**, 16 failed | **404 passed**, 16 failed (+11 = +5 коммит A + 6 time-sensitive flaky теперь passed потому что время после 09:00 UTC) |
| `apps/worker/tests/test_close_catch_window.py` | 4 failed (pre-existing) | 4 failed (unchanged) | **4 failed** (unchanged) |
| `apps/worker/tests` (полный) | 9 failed, 77 passed | 9 failed, 77 passed | **9 failed, 77 passed** (unchanged) |

## Production state (snapshot 2026-08-17, HEAD `fffa7c4`)

**На проде развёрнуты все 5 функциональных коммитов + chore:**

- `3796531` (feat/constants) — добавлен `PenaltyReason.WAIVED_NO_DEPOSIT` (переименован в #A)
- `5bfeec7` (feat/penalty) — `apply_window_expired` пишет WAIVED для ACTIVE+deposit=0
- `241115f` (feat/penalty) — `apply_catch` idempotency на все reason'ы (бонус: закрыта дыра WINDOW_CLOSED_NO_CATCH+catch)
- `4dc2b08` (refactor/worker) — удалён мёртвый дубль в `close_catch_window`
- `9c32d6f` (feat/penalty) — **основное закрытие дыры**: rename enum + `mark_waived_unable_to_pay` для PAUSED + интеграция в cron + счётчик `waived`
- `9fe3fbc` (docs) — синхронизация документов с фактическим состоянием
- `fffa7c4` (chore/e2e) — удаление одноразового E2E-скрипта после успешного прогона на Софье

**E2E на реальных данных Софьи (id=5361424459) — PASSED:**
- Софья PAUSED, deposit=0 → cron → 2 WAIVED маркера создано (клубы с закрытым окном; третий клуб 'Чтение' был `skipped: window_open` — by design)
- 0 Transaction с amount=0 за тот же интервал
- Topup (0→25000), ACTIVE → catch за этот день → `PenaltyAlreadyProcessedError(code=penalty_already_processed)`
- Депозит не списан (остался 25000) — дыра закрыта

### Rollback (точка отката)

**Безопасный откат — на `eaffd9d`** (pre-PR, до `3796531`):

```bash
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev reset --hard eaffd9d
# Затем повторить шаги deploy: rsync + build backend/worker --no-cache + up -d.
# ⚠️ ВАЖНО: после отката старая `apply_catch` снова имеет PRIMARY дыру
# (фильтр reason == CAUGHT, маркеры в БД больше не пишутся). Дыра
# возвращается — задокументированный trade-off отката.
```

**Частичных откатов НЕТ** (см. анализ в чате 2026-08-17). Промежуточные состояния:
- `3796531` (только enum value, без логики) — бессмысленно
- `3796531`+`5bfeec7` (ACTIVE+deposit=0 path) — ХУДШЕЕ: маркеры пишутся, но apply_catch их не видит (PRIMARY дыра ОТКРЫТА хуже чем было)
- `241115f` (расширенная idempotency) — runtime-safe, PRIMARY дыра всё ещё открыта
- `4dc2b08` (refactor) — runtime-safe, PRIMARY дыра открыта

Только `eaffd9d` (полный pre-PR) или `fffa7c4` (финал PR) — безопасные состояния.

## Production verify (после деплоя)

```bash
# 1. alembic upgrade head (НЕ требуется — миграции нет)
ssh privichki-prod 'cd /app/infra && docker compose exec backend alembic current'
# → 015_checkin_status_extra_values (head без изменений)

# 2. rsync + build + up только backend + worker (image-based)
ssh privichki-prod 'rsync -az --delete /tmp/privichki_new/backend/ /app/apps/backend/'
ssh privichki-prod 'rsync -az --delete /tmp/privichki_new/worker/  /app/apps/worker/'
ssh privichki-prod 'cd /app/infra && docker compose build backend worker --no-cache && docker compose up -d backend worker'

# 3. Проверка что новое значение enum видно в работающем контейнере
ssh privichki-prod 'docker exec habit-backend python -B -c "from app.core.constants import PenaltyReason; print(PenaltyReason.WAIVED_UNABLE_TO_PAY.value)"'
# → waived_unable_to_pay

# 4. (опционально) ручной сценарий на проде: создать тестового юзера, обнулить
# deposit, дождаться cron close_catch_window (ежечасно в :05), убедиться что
# в таблице penalties появилась строка с reason='waived_unable_to_pay'.
```

## Backlog (отдельные задачи, НЕ в этом PR)

### `BL-001`: CHECK constraint на `penalties.reason`

Defense-in-depth на уровне схемы БД. Сейчас `reason` VARCHAR без constraint,
можно теоретически записать произвольную строку.

```sql
ALTER TABLE penalties 
ADD CONSTRAINT chk_penalty_reason 
CHECK (reason IN ('caught', 'window_closed_no_catch', 'waived_unable_to_pay'));
```

Перед применением на проде: `SELECT DISTINCT reason FROM penalties;` —
убедиться, что нет «мусорных» значений. По разведке — только три известных
значения, должно пройти чисто. **Отдельная задача** «укрепить схему БД».

### `BL-002`: UI-различие «штраф списан» vs «штраф прощён»

Сейчас `TodayPage` показывает «штраф не списан» и для WAIVED, и для
«день ещё не наступил как missed». Если захотим показать
«Прощён (депозит был пуст)» явно — расширить `TodayResponse` полем
`penalty_outcome: 'charged' | 'waived_unable_to_pay' | 'none'`, добавить
text-key в `shared/texts/`. Дизайн-решение требуется.

### `BL-003`: расширить `CheckinEvent.payload` полем `checkin_status`

Известная проблема (см. `docs/09-prod-readiness.md` §1.1, строка 242):
бот и мини-апп показывают РАЗНЫЕ формулировки для `caught_today`
(cron `apply_window_expired` vs `apply_catch`). Наш фикс эту разницу
**не устраняет** — нужно расширить `_publish_checkin_rejected` и маппер
на фронте. **Отдельный PR.**

### `BL-004`: Audit-trail для `WAIVED_UNABLE_TO_PAY`

Потенциально полезный observability-сигнал — записывать в `audit_log`
(если появится) или отдельную таблицу
`window_expired_waived(user_id, habit_id, club_date, deposit_balance_at_close)`.
Может пригодиться для антифрод-эвентов или продуктовой аналитики
«сколько юзеров в принципе не могут позволить штраф». **Backlog idea.**

### `BL-005`: worker test-infra bug — `User.deposit_balance` не устанавливается в `add_membership(deposit_balance=X)`

**Pre-existing** (воспроизводится на `eaffd9d` и `a08f08a`). После
миграции 014a (`014a_user_deposit.py`) deposit живёт на `users.deposit_balance`,
но `add_membership(deposit_balance=...)` помечен как backward-compat no-op
(`apps/worker/tests/conftest.py:333-336`). Тесты НЕ устанавливают
`User.deposit_balance`, поэтому он остаётся 0 (SQLite не применяет
`server_default`).

**Затронуто 9 worker-тестов** (`test_close_catch_window.py` × 4,
`test_close_season.py` × 1, `test_process_checkin.py` × 1,
`test_process_penalty.py` × 2, `test_worker_cron_chain.py` × 1) —
все ожидают `penalty.reason == WINDOW_CLOSED_NO_CATCH` или `penalized=1`,
получают `WAIVED_UNABLE_TO_PAY` или `penalized=0`.

**Решение (отдельный PR):**
- Добавить параметр `deposit_balance: int = 1000` в `add_user` helper.
- Обновить все 9 тестов — убрать `deposit_balance=X` из `add_membership`,
  перенести в `add_user`.
- Заодно проверить что нет других полей, мигрировавших из `membership`
  в `user` (по 014a/014b — `deposit_balance` единственная).

**Не блокирует этот PR** — дыра test-infra, не production. Но мешает
CI-сигналу о worker-регрессиях.

### `BL-006`: time-sensitive флаки-тесты в `test_joined_late_protection.py`

**Pre-existing** (обнаружен при прогоне в ходе PR #1 разведки 2026-08-17
утром, но корневая причина была заложена ещё в коммите `497d01d` Z-19).
6 тестов в `apps/backend/tests/test_joined_late_protection.py` жёстко
прописывают окно чек-ина `window_start_h=6, window_end_h=12` (MSK time)
через `_make_habit_with_window()` и используют `joined_at = datetime.now(tz=UTC) - timedelta(minutes=5)`.

**Когда тесты проходят:**
- Локальное время (UTC) **после** `09:00` (= `12:00 MSK = window_end`).
- В это время `joined_at` оказывается после `window_end_h`, срабатывает
  `CheckinJoinedLateError` → тест зелёный.

**Когда тесты падают (наблюдалось 2026-08-17 в 10:51 MSK = 07:51 UTC):**
- Локальное время (UTC) **между** `03:00` и `09:00` (= MSK 06:00-12:00
  внутри окна). `joined_at = now - 5min` тоже внутри окна → joined_late
  не срабатывает → тест падает с `DID NOT RAISE CheckinJoinedLateError`.

**Затронутые тесты:**
- `test_joined_late_takes_precedence_over_window_closed`
- `test_habit_state_response_marks_joined_late`
- `test_process_checkin_raises_joined_late`
- `test_process_checkin_joined_late_no_db_writes`
- `test_normal_user_in_window_unchanged` (тоже — поведение зависит от
  того, внутри окна или после)
- `test_joined_late_with_joined_at_none_does_not_crash`

**Почему это всплыло именно сейчас (в ходе этого PR, не раньше):**
В ходе разработки #1-#4 вечером воскресенья 2026-08-16 время было после
`window_end` (UTC > 09:00) — все тесты проходили, baseline=393 passed.
Утром понедельника 2026-08-17 (UTC 07:51 < 09:00) — 6 тестов падают,
baseline=393 passed (те же самые тесты, флаки-поведение от wall-clock).
Никакой связи с моими изменениями — `git stash` подтверждает.

**Решение (отдельный PR):**
- Сделать `now_utc` параметром тестов через `freezegun` или явный
  `monkeypatch.setattr(datetime, "now", ...)`.
- Или вычислять `joined_at` относительно `window_end_h` (например,
  `window_end + 1 минута`), а не `now`.
- Или прогонять тесты в CI в определённое время суток (хак, не рекомендуется).

**Workaround до фикса:** запускать `test_joined_late_protection.py` вечером
(после 12:00 MSK). Или просто игнорировать эти 6 failures в baseline —
они pre-existing, не регрессия от текущих изменений (проверено stash'ом).

### `BL-007`: subscription expiry → LEFT (новый feature, не в этом PR)

**Pre-investigation 2026-08-17** (юзер поднял задачу, разведка сделана до
стопа). Зафиксировано для следующего захода, **не делаем сейчас** — сначала
закрываем WAIVED-маркер для PAUSED/LEFT (этот PR).

**Что хочет юзер:** когда `subscription_until < today`, юзер переходит в
`LEFT` (НЕ удаляется из клуба как участник, мембершип сохраняется), в UI
появляется кнопка "Продлить участие" и списание подписки. Логика оплаты:
- Если `deposit >= habit.penalty_amount` → оплачивается только подписка
- Если `deposit < habit.penalty_amount` → подписка + депозит (как сейчас)

**Что нужно построить:**

| Компонент | Что |
|---|---|
| **Backend: новая cron-таска** `worker.tasks.expire_subscriptions.run` | Ежедневно (02:00 UTC, до `expire_bonus_points`). Находит `Membership(status=ACTIVE, subscription_until < today)` → переводит в `LEFT`. Skip PAUSED/LEFT (LEFT уже там, PAUSED не управляется подпиской). |
| **Backend: новый метод** `MembershipService.expire_subscription(membership_id)` | Defensive проверки, `m.status = LEFT`, не трогать deposit. Идемпотентно (повторный вызов на LEFT — no-op). |
| **Backend: новая notification** `NotificationType.SUBSCRIPTION_EXPIRED` | Опционально — Telegram-сообщение юзеру. i18n через `apps/bot/bot/texts/subscription_expired.py`. |
| **Backend: расширение `subscribe_and_join`** | Поддержка "только подписка" если `deposit >= penalty_amount` (только для renew, не для new join). Возможно — новый параметр `mode: "renew_subscription_only" \| "renew_with_deposit" \| "first_join"`. **Решение по API ещё не принято.** |
| **Frontend: бейдж "Подписка истекла"** в `apps/frontend/src/pages/Profile/index.tsx` | Красный бейдж + кнопка "Продлить участие" для membership `status=LEFT` с `subscription_until < today`. |
| **Frontend: баннер на `/habits/:id/today`** | Если `subscription_until < today` — баннер: "Подписка истекла, продлите участие". |
| **Frontend: filter в `/habits/:id/members`** | LEFT-юзеры НЕ показываются в списке "можно поймать" (сейчас `can_catch` не проверяет `membership_status`). Отдельная правка UI noise. |
| **Docs: обновления** | `docs/04-code-standards.md` (grace period если решим ввести), `docs/09-prod-readiness.md` (новая строка в таблице). |

**Открытые вопросы (требуют решения перед реализацией):**

1. **Reaction activation:** `subscribe_and_join` уже умеет реактивировать LEFT→ACTIVE
   (кейс 3a, services/membership_service.py:332-336). Отдельный endpoint не нужен —
   юзер жмёт "Продлить участие" → открывается `JoinPayModal` → POST /payments/subscribe.
2. **Логика "только подписка":** автоматическая ветка в `subscribe_and_join` или
   новый параметр от UI. **Решение не принято.**
3. **Уведомление при истечении:** (I) Сразу в Telegram-бот, (II) только в UI при
   следующем заходе, (III) оба.
4. **Grace period** (дни между `subscription_until < today` и фактическим LEFT):
   (I) жёстко день в день, (II) N дней (например 3).
5. **Где показывать бейдж "продлить":** (I) `/profile`, (II) `/habits/:id/today`,
   (III) `/habits/:id/members`, (IV) все три.
6. **Что делать с уже существующим долгом** (если deposit < penalty и подписка
   истекла): тот же сценарий "нужна и подписка и депозит".

**Связь с WAIVED-маркером (BL из этого PR):**
- Если BL-007 вводится до того, как закрыт WAIVED-маркер для PAUSED/LEFT,
  это **порождает** новые случаи дыры: cron `expire_subscriptions` переводит
  ACTIVE→LEFT → следующий cron `close_catch_window` пропускает (LEFT skip)
  → юзер топит депозит → его можно поймать. **Дыра расширяется.**
- Поэтому BL-007 делается **после** того, как WAIVED-маркер покрывает PAUSED/LEFT
  (этот PR).

**Workaround до BL-007:** юзеры с протухшей подпиской могут вручную
перезайти через `POST /payments/subscribe` (бэкенд уже умеет, кейс 3a).
Бот-уведомлений нет, нужно самим заходить в мини-апп.

**Текущее состояние прод-данных (2026-08-17):** Sofia — единственный
пользователь с множественными memberships (3). У неё подписка скорее всего
недавно (joined_at 2026-08-14/15). Реальных случаев "subscription_until < today"
на проде пока нет (софья не дошла до 30-дневного лимита). Можно не спешить.

**Оценка:** средняя задача, 4-6 атомарных коммитов, ~3-5 дней работы.
Не блокирует WAIVED-маркер для текущей итерации.

## История решений

- **2026-08-16:** разведка подтвердила наличие финансовой дыры
  (см. начало чата — 4 пункта разведки).
- **2026-08-16:** юзер выбрал **Вариант A** (минимальный — маркерный Penalty),
  не B (CHECK constraint) и не C (миграция на Postgres ENUM).
- **2026-08-16:** в процессе реализации выяснилось, что `penalties.reason`
  это VARCHAR (не ENUM). **Первоначальный план (Alembic миграция с `ALTER TYPE`)
  не нужен.** Изменён план коммита #1: только `constants.py`.
- **2026-08-17:** все 4 атомарных коммита + docs закрыты. Готово к деплою.