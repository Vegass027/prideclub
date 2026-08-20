# Pravki-deposit-sse.md — финальный план Z-1..Z-11

> Snapshot 2026-08-07 (вечер, после проверки проде). Финальная редакция после полного ревью:
> 4 правки A/B/C/D + 4 ответа Q1-Q4 + 1 архитектурное дополнение по
> мультиплексированию стримов + 4 ответа Q5-Q8 + Z-2.8 (defense-in-depth
> в API catch-handler от регрессии двойного catch).
> Документ для реализации. Никакого кода — текстовый план с указанием файлов,
> контрактов, hot-path оценок и тестов.
>
> Предыдущая версия плана: см. §1 ниже, summary изменений.
>
> **Snapshot 2026-08-08 (PR #1 реализован, ожидает «ок» на push).** Закоммичено в
> `ac6951f feat(backend): Pravki-deposit-sse Z-1+Z-2+Z-2.8 — global deposit on users`.
> Подробности и точечные расхождения с планом — см. §Z-2.9.

> **Snapshot 2026-08-14, race-fix и frontend filter.** Серия из 4 коммитов в
> `feature/paused-member-ux` (`dfa3b2c` + `1f86217` + `7a988f8` + `a6cf949`):
>
> 1. **`dfa3b2c fix(backend): re-check violator.status after user-lock in apply_catch`** —
>    обновление §Z-2.4. После `lock_for_update(user)` добавлены
>    `await self._session.refresh(violator)` + повторная проверка `MembershipNotActiveError`.
>    Закрывает race-окно, в котором параллельная транзакция могла переключить
>    `membership.status` через `recompute_pause_status` и закоммитить. Финансово
>    защита была и до (amount-guard), но семантически inconsistent — Penalty
>    создавался для жертвы с `membership.status != ACTIVE`. Тест
>    `test_apply_catch_rereads_violator_status_after_user_lock` через
>    `RaceyUserRepo` (мутирует `violator.status` во время `lock_for_update`)
>    ловит регрессию: без фикса тест падает с `DID NOT RAISE MembershipNotActiveError`.
>
> 2. **`1f86217 fix(bot): правдивый paused+window_open copy + restore merge indentation`** —
>    обновление §Z-5.1 (bot pre-filter для paused). Старый `REJECT_PAUSED_OR_WINDOW`
>    жёстко говорил «окно закрыто» для ВСЕХ paused-кейсов. Добавлен
>    `REJECT_PAUSED_WINDOW_OPEN` для случая paused + окно открыто:
>    «депозит пуст, но окно ещё открыто ({start}–{end}), пополни сейчас и
>    успеешь чек-ин сегодня». В `_prefilter()` разветвление по
>    `state.is_within_checkin_window`. Variant B для приоритетов: единственная
>    ветка paused+left переехала на позицию между `joined_late` и
>    `window_closed` — сохраняет §Z-22 контракт `caught_today (#3) > paused (#6)`,
>    но для paused+closed показывает объединённый текст. Бонус: восстановлен
>    `IndentationError` на строке 209 (после merge-конфликта `dae137b9`
>    2026-08-13 21:16) — без этого фикса бот не импортировался бы при следующем
>    рестарте контейнера.
>
> 3. **`7a988f8 fix(frontend): hide paused members from catch list + connect SSE invalidate`**
>    — обновление §Z-6.5 (frontend SSE) и `apps/frontend/src/pages/Members/MembersPage.tsx`.
>    Backend `MemberRowOut` теперь возвращает `membership_status: str = "active"`
>    (defensive default, backward-compatible). Frontend `MembersPage` фильтрует
>    violators по `m.status === 'missed' && m.can_catch && m.membership_status === 'active'`,
>    и `<MemberRowItem>` получает `onCatch` только когда `m.membership_status === 'active' &&
>    m.can_catch` — paused-юзер остаётся в общем списке без кнопки «Поймать».
>    `useHabitSse(habitId)` подключён к `MembersPage` (раньше только в `TodayPage`)
>    — real-time invalidate на `catch` event через `streamController.ts:201-214`.
>
> 4. **`a6cf949 test(frontend): add MembersPage tests for paused filter`** —
>    vitest-тесты для `MembersPage.test.tsx` (4 кейса: paused, active, left, mixed).
>    Мимоходом нашли баг в коммите 3: `<MemberRowItem>` рендерил кнопку «Поймать»
>    в общем списке по `can_catch` без учёта `membership_status`. Исправлено
>    через `--amend` в том же коммите 3 (frontend fix), тесты вошли отдельным
>    коммитом 4.
>
> Подробности и точечные расхождения с планом — см. `docs/AGENT_BOOTSTRAP.md` §9
> (snapshot A/B/C).

---

## 0. Контекст и принципы

- **Стек:** Python 3.12 + FastAPI 0.115 + SQLAlchemy 2.0 + asyncpg + aiogram 3.30 + Celery 5.4 + Redis 7 + React 18 + TS 5. Все правила из `AGENTS.md` применяются (layered architecture, DI через конструктор, `int` копейки, `user_id` только из `request.state.telegram_user`, PII не логировать, доменные исключения).
- **SSE-инфра уже есть и задеплоена** для чек-инов (`docs/archive/2026-summer-fixes/sse+redis.md` редакция 9, реализованная Steps 1-6). Контракты `stream_key`/`EventPublisher.publish_checkin` (актуальное имя метода в коде, **НЕ** `publish_to_user` — это была ошибка в ранних редакциях плана)/XREAD BLOCK 30s/`SseConnectionLimiter` — зафиксированы. Z-6/Z-7 расширяют, не переписывают.
- **Telegram-бот уже работает с pre-filter** для длительности кружка и forwarded-сообщений. Z-5 добавляет четвёртый pre-filter — на депозит.
- **Production snapshot 2026-08-07:** 0 юзеров с реальными деньгами на депозитах. Миграция 014 — низкий риск, но ритуал `pg_dump` всё равно соблюдаем (привычка).

---

## 1. Сводка изменений от предыдущей версии

| # | Что | Где | Было | Стало |
|---|---|---|---|---|
| A | Единый порог 1× penalty | Z-3, Z-4, Z-5 | `required = penalty * 4` | `required = penalty * 1` |
| B | Глобальный recompute_pause_status | Z-2.6 | per-membership `PAUSED` в `apply_catch` (rollback откатывал, воркер дублировал) | `MembershipService.recompute_pause_status(user_id)` — централизованно пересчитывает ВСЕ активные membership'ы юзера после каждого изменения `user.deposit_balance` |
| C | `pg_dump` перед миграцией 014 | Z-2 §0 | в «Сквозном риске» в конце | §0 раздела Z-2 — первым шагом |
| D | Broadcast через habit-strim | Z-6, Z-7 | `publish_to_habit` = N XADD через pipeline (fan-out) | `publish_to_habit` = 1 XADD в `sse:habit:{habit_id}`; клиенты мультиплексируют user-strim + habit-strim в одном XREAD |
| D+ | Два курсора Last-Event-ID | Z-6.3, Z-6.5 | один `last_event_id` для одного стрима | `last_event_id_user` + `last_event_id_habit`; backend и frontend трекают оба независимо |
| Q5 | Backward-compat для старого `last_event_id` | Z-6.3.5 | `last_event_id` (один query) | **ИСПРАВЛЕНО 2026-08-07 после проверки проде:** клиент УЖЕ на проде (Steps 1-6 задеплоены ~2026-08-04, см. `Pravki.md §7.8`). Старый `last_event_id` нужно оставить как fallback. Сервер: если переданы `last_event_id_user`/`last_event_id_habit` → мультиплекс; если только `last_event_id` → legacy single-stream (только user-strim). |
| Q6 | Backward-compat для SSE-токена `scope` | Z-6 | `scope="sse:today"` | **ИСПРАВЛЕНО 2026-08-07 после проверки проде:** клиент УЖЕ на проде, токены с `scope="sse:today"` живой код выпускает и валидирует. Менять константу `SSE_TOKEN_SCOPE` = breaking change (все живые токены станут невалидными → 401). Нужно оставить `scope="sse:today"` в token-валидаторе, добавить опциональный claim `habit_events: true` для новых токенов, принимать оба варианта. |
| Q7 | Защита от дублей DM | Z-10.3 | только `Penalty.notify_sent` (rollback-опасный) | Redis-guard `dm_sent:{penalty_id}` (TTL 7 дней, Guard-паттерн как `sse_published:*`) + `Penalty.notify_sent` остаётся для аудита/отображения в админке |
| Q8 | Текст DM violator | Z-10.1 | без явного решения | статичный текст (без ротации/AI-коменданта) |
| META | Имена методов в плане | Z-6.2, Z-6.4 | `publish_to_user` (выдуманное) | **ИСПРАВЛЕНО 2026-08-07 после чтения реального кода:** актуальное имя — `publish_checkin(*, user_id, habit_id, membership_id, date_iso, event: CheckinEvent) -> bool`. `publish_to_habit` — НОВЫЙ метод, добавляется рядом. `you_were_caught` публикуется через существующий `publish_checkin` (idempotency per (membership, date) — естественно per-юзер-per-день). |
| RACE | Defense-in-depth в API catch-handler | Z-2.8 (новый) | `except Exception` без обработки `IntegrityError` → 500 на edge-case | явный `except IntegrityError → CatchResponse(ok=False, code="penalty_already_processed")` |
| COLLISION | Idempotency namespace collision | Z-6.2, Z-6.4 | `idempotency_key(m, d) → sse_published:checkin:{m}:{d}` (жёстко зашитый литерал `"checkin"`) | **ИСПРАВЛЕНО 2026-08-07 после ревью пользователя:** добавлен keyword-only kwarg `event_type: str = "checkin"` в `idempotency_key()` и `publish_checkin()`. Существующие call-сайты (`process_checkin.py:112, 150`) не передают параметр → дефолт `"checkin"` → ключ байт-в-байт идентичен старому, существующие тесты в `test_event_publisher.py` не ломаются. Новый вызов для `you_were_caught` передаёт `event_type="caught"` явно → `sse_published:caught:{m}:{d}` — независимый namespace. Без этого фикса утренний `checkin.rejected` забивал ключ `sse_published:checkin:{m}:{d}` на 24ч (TTL 86400), и вечерний `you_were_caught` для той же `(m, d)` молча терялся (SET NX → False → XADD не выполняется, без exception, только INFO-лог). Регрессионный тест `test_you_were_caught_does_not_collide_with_checkin_rejected_same_day` обязателен. |

**Q1 (принят):** `recompute_pause_status` в той же транзакции. User-lock автоматически сериализует параллельные изменения баланса, отдельные `FOR UPDATE` на membership не нужны.

**Q2 (принят):** вариант A (мультиплексирование двух стримов в одном XREAD).

**Q3 (принят):** `idempotency_key = lb:{habit_id}:{club_date}:{penalty_id|checkin_id}`.

**Q4 (принят):** Z-11 в PR #2.

**Архитектурное дополнение:** Last-Event-ID сломан при мультиплексировании. SSE-протокол поддерживает ровно одно значение `Last-Event-ID` на соединение. Redis Stream ID из одного стрима не имеет смысла как курсор для другого. Решение — два независимых курсора в URL и в состоянии клиента. Подробности — §Z-6.3.1.

**Регрессионный пробел (Z-2.8):** `apps/backend/app/api/v1/members.py:204-209` ловит `Exception`, но `sqlalchemy.exc.IntegrityError` — не `DomainError`. Если UNIQUE constraint `uq_penalty_per_day_reason` сработает в API endpoint'е (любая будущая гонка или регрессия), это станет 500 для пользователя. Worker (`apps/worker/worker/tasks/process_penalty.py:190-193`) ловит IntegrityError корректно, API — нет. После Z-2.4 (user-lock вместо membership-lock) race в happy-path не возникает, но защита на этот случай обязательна.

**Ложная посылка в первоначальном плане (исправлено 2026-08-07 после проверки проде):** «SSE-клиент для чек-инов ещё не задеплоен на проде». Утверждение было основано на устаревшем снэпшоте `docs/archive/2026-summer-fixes/sse+redis.md` (редакция 9, dated 2026-08-04), где Steps 1-4 помечены как ❌ НЕ задеплоены. На проверке 2026-08-07:
- `events.py` присутствует в контейнере `habit-backend` (`/app/apps/backend/app/api/v1/events.py`),
- `events.router` смонтирован в `app/main.py:168` (`app.include_router(events.router, prefix="/api/v1", tags=["events"])`),
- `SSE_AUTH_BYPASS_PATHS = {"/api/v1/events/stream"}` в `core/middleware.py` (строки 44, 72, 240),
- `SSE_TOKEN_SECRET` и `SSE_TOKEN_TTL_SECONDS=60` в env контейнера,
- Frontend bundle `main-CHs1AelX.js` содержит SSE-клиент (Step 6 скомпилирован в прод-JS),
- Контейнеры `habit-{backend,frontend,bot,worker}` `Up 3 days` (rebuild от ~2026-08-04, синхронно с фиксом nginx из `Pravki.md §7.8`),
- Live-эндпоинты: `GET /api/v1/events/stream` → 422 (валидация query, не 404), `POST /api/v1/events/stream/token` → 401 (auth, не 404).

Steps 1-6 реально задеплоены и работают. Это сломало первоначальные посылки **Q5 и Q6** (оба планировались как breaking change «клиента нет — можно сломать»). Оба исправлены в вариант B с backward-compat (см. §4 и Z-6.3.5).

**Frontend bundle pattern (безопасен для broadcast):** в `main-CHs1AelX.js` legacy-клиент `useTodayStream` использует только `addEventListener("checkin.accepted", ...)` и `addEventListener("checkin.rejected", ...)`. Никаких generic `onmessage` или «process any event»-обработчиков. Новые типы событий (`catch_event`, `leaderboard_update`, `you_were_caught`), которые полетят в мультиплексированном потоке после PR #4, будут проигнорированы старым клиентом — безопасный паттерн. Pre-deployment проверка на регрессию зафиксирована в Z-6.6.

---

## 2. Карта зависимостей (финальная)

```
PR #1: Z-1 + Z-2   (фундамент: bug fix + перенос депозита на users.deposit_balance
                    + Alembic 014 + recompute_pause_status)
    ↓
PR #2: Z-3 + Z-4 + Z-11   (UI: join-modal с порогом + open-club-disabled + wallet endpoint
                            + TopUpModal без radio + cleanup Members headerRight)
    ↓
PR #3: Z-5   (bot pre-filter на депозит + worker defense-in-depth + internal_users endpoint)
    ↓
PR #4: Z-6 + Z-7   (broadcast через habit-strim + два курсора Last-Event-ID + useHabitSse)
    ↓
PR #5: Z-9 + Z-10   (anti-fraud: already_caught в members + DM violator + notify_sent)
    ↓
PR #6: Z-8   (zero_count в leaderboard response + UI «+N скрыты»)
```

---

## Z-1. Bug fix: catch «deposit_exhausted» после первой успешной поимки

**Severity:** P0 (блокирует прод-сценарий «поймать соседа»).

**Симптом:** первый catch проходит, последующие в других клубах или в этом же возвращают `code: "deposit_exhausted"` даже если в профиле юзера деньги есть.

**Корневая причина:** депозит хранится **на `memberships.deposit_balance`** (`apps/backend/app/models/membership.py:47`, будет удалён в Z-2). Юзер пополняет депозит через `POST /api/v1/payments/topup` с привязкой к membership (radio-выбор клуба, `apps/frontend/src/shared/ui/TopUpModal.tsx`). Если membership жертвы для этого клуба никогда не пополнялась — там `0` → `amount = min(penalty, 0) = 0` → `raise PenaltyAlreadyProcessedError("deposit_exhausted")` (`apps/backend/app/services/penalty_service.py:101`).

**Фикс:** Z-1 и Z-2 — один баг, разные слои. Z-2 полностью его лечит.

---

## Z-2. Data model: глобальный депозит на пользователя

**Severity:** P0 (фундамент). Блокирует Z-3, Z-4, Z-5, Z-9, Z-10.

**Цель:** один депозит на пользователя, общий для всех клубов. Логика «закончился — замораживаем клуб» — централизованно в `MembershipService.recompute_pause_status`.

### §0. Подготовка к миграции

**Сначала — `pg_dump` на сервере.** Не блокирует (сегодня на проде 0 юзеров с реальными деньгами, терять нечего), но привычка на будущее.

Алгоритм:
1. `ssh privichki-prod` → `docker exec habit-postgres pg_dump -U postgres -d habitclub | gzip > /tmp/pre_migration_014_backup.sql.gz`.
2. Скопировать к себе: `scp privichki-prod:/tmp/pre_migration_014_backup.sql.gz ./`.
3. Проверить: `gunzip -c ./pre_migration_014_backup.sql.gz | head -20`.

Время: ~10 секунд. Делаем перед alembic upgrade.

### Z-2.1 Alembic-миграция 014

**Файл:** `apps/backend/alembic/versions/014_user_deposit_balance.py`

Двухшаговая (безопасная):

**Шаг 1 (up):** `ALTER TABLE users ADD COLUMN deposit_balance BIGINT NOT NULL DEFAULT 0`. Заполнение:
```sql
UPDATE users
SET deposit_balance = COALESCE(
  (SELECT SUM(deposit_balance) FROM memberships
   WHERE user_id = users.id AND status = 'active'), 0
);
```

Sanity-проверки (отдельный блок в `upgrade()` через `op.execute("SELECT ...")`):
- `SELECT COUNT(*) FROM users WHERE deposit_balance < 0` → 0.
- `SELECT COUNT(*) FROM users WHERE deposit_balance > 0 AND NOT EXISTS (SELECT 1 FROM memberships WHERE user_id = users.id AND status = 'active')` → 0 (юзеры с деньгами но без активных клубов — это деньги в никуда).
- `SELECT SUM(deposit_balance) FROM users` == `SELECT SUM(deposit_balance) FROM memberships` (до миграции).

Если sanity-проверка падает — миграция raise'ит, БД остаётся в консистентном состоянии (только ADD COLUMN, без DROP).

**Шаг 2 (up):** `ALTER TABLE memberships DROP COLUMN deposit_balance`. Делать в **отдельном коммите** через `op.execute("COMMIT"); op.execute("BEGIN")` или, безопаснее, разделить на две миграции (`014a` и `014b`). Рекомендация: **две миграции** — это даст окно в 1+ день между шагами для проверки что нигде в коде не сломалось.

`downgrade()` откатывает оба шага (восстанавливает `memberships.deposit_balance` из `users.deposit_balance`). Помечаем как `downgrade_safe=True` (это уже практика — см. остальные миграции).

### Z-2.2 Изменения моделей

**`apps/backend/app/models/user.py`** — добавляем:
- `deposit_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")`.

**`apps/backend/app/models/membership.py`** — удаляем:
- строку 47 (поле `deposit_balance`).

Поле `bonus_points` остаётся per-membership (это отдельная сущность — награда за ловлю, не относится к депозиту).

### Z-2.3 Репозитории

**`apps/backend/app/repositories/user_repository.py`** (новый файл):
- `async def lock_for_update(user_id: int) -> User` — `SELECT ... FOR UPDATE WHERE id = :user_id`.
- `async def get(user_id: int) -> User | None` — простой SELECT без лока.
- `async def add_balance(user_id: int, amount: int) -> None` — `lock_for_update` + `deposit_balance += amount`.

**`apps/backend/app/repositories/membership_repository.py`** — удалить `add_balance()` (строки 100-103). `lock_for_update_by_user_habit` остаётся для `payment_service._apply` (для bonus_points/lock-for-update, без deposit).

### Z-2.4 `PenaltyService.apply_catch`

**Файл:** `apps/backend/app/services/penalty_service.py`

Логика изменений:
- Убираем `lock_for_update_by_user_habit(violator_membership_id)` (старый `violator = await self._membership_repo.lock_for_update(violator_membership_id)`).
- Вместо него: `violator = await self._membership_repo.get(violator_membership_id)` (без лока, просто чтение).
- `violator_user = await user_repo.lock_for_update(violator.user_id)` — **один лок на user**, сериализует все параллельные catch/topup этого юзера.
- `amount = min(habit.penalty_amount, violator_user.deposit_balance)`.
- Если `amount <= 0` → raise `PenaltyAlreadyProcessedError("deposit_exhausted", code="deposit_exhausted")`. Дальше Z-2.6 (recompute_pause_status) выставит PAUSED для всех клубов юзера, где deposit < penalty.
- `violator_user.deposit_balance -= amount`.
- `add_to_prize_pool(...)` — без изменений.
- `penalty` row — без изменений.
- `transaction` row — без изменений.

**Hot path:** один `SELECT FOR UPDATE` на user (вместо одного на membership). Это автоматически (см. Q1) сериализует параллельные catch в разных клубах одного юзера — два catch не могут одновременно списать депозит.

**Defense-in-depth: race-fix от 2026-08-14 (commit `dfa3b2c`).** Между `violator = await self._membership_repo.get(...)` (без лока, шаг выше) и `lock_for_update(user)` существует окно гонки: параллельная транзакция (другой catch этого юзера или cron `apply_window_expired`) могла изменить `membership.status` через `recompute_pause_status` и закоммитить. После `lock_for_update(user)` код сериализован с этими операциями, но `violator` объект — из identity map SQLAlchemy, загружен **до** лока. Без `refresh` использовался бы staled статус. Финансово `amount-guard` ниже ловит (если balance уже обнулён, `min(penalty, 0)=0` → reject), но семантически inconsistent — Penalty создавался для жертвы с `membership.status != ACTIVE`. Фикс:

```python
violator_user = await self._user_repo.lock_for_update(violator.user_id)
assert violator_user is not None, "violator membership has no user"

# Pravki-paused-race-2026-08-14: re-read под user-lock'ом.
await self._session.refresh(violator)
if violator.status != MembershipStatus.ACTIVE:
    raise MembershipNotActiveError()
```

Один дополнительный `SELECT` (~1ms overhead). Тест `test_apply_catch_rereads_violator_status_after_user_lock` через `RaceyUserRepo` (мутирует `violator.status` во время `lock_for_update`) подтверждает лов — без фикса тест падает с `DID NOT RAISE MembershipNotActiveError`. **Это orthogonal к §Z-22 canonical order priority** — race-fix не меняет порядок проверок, только добавляет повторную проверку статуса.

### Z-2.5 `PaymentService._apply` (topup)

**Файл:** `apps/backend/app/services/payment_service.py`

Было: `m = await membership_repo.lock_for_update_by_user_habit(user_id, habit_id); m.deposit_balance += amount`.

Стало: `u = await user_repo.lock_for_update(user_id); u.deposit_balance += amount`.

Контракт `/api/v1/payments/topup` меняется:
- **Не принимает `habit_id`** в request body.
- Пополняет `user.deposit_balance` глобально.
- Frontend `TopUpModal` теряет radio-выбор клуба (см. Z-3.3, Z-4.3).

**Hot path:** тот же `SELECT FOR UPDATE` на user — lock_for_update из `apply_catch` и из `_apply` сериализуются через одну и ту же запись.

### Z-2.6 Новый метод `MembershipService.recompute_pause_status(user_id)`

**Файл:** `apps/backend/app/services/membership_service.py`

```text
async def recompute_pause_status(self, *, user_id: int) -> None:
    """Пересчитать status всех активных memberships юзера после изменения deposit_balance.
    
    Правило (Q1 — обосновано user-lock'ом):
    - Один SELECT на user (уже залоченный вызывающим кодом через UserRepository.lock_for_update)
    - Один SELECT на список ACTIVE memberships юзера
    - Для каждой membership: один SELECT на habit (для penalty_amount)
    - Если user.deposit_balance < habit.penalty_amount → status = PAUSED
    - Иначе → status = ACTIVE (на случай если был PAUSED из-за предыдущего расчёта)
    - LEFT membership не трогаем
    
    Транзакция: одна. Сервис не коммитит (commit на уровне handler'а).
    Дополнительные FOR UPDATE на memberships НЕ нужны — user-lock уже исключает
    конкурентный доступ ко всем memberships этого юзера в этой транзакции.
    """
```

**Конструктор MembershipService расширяется:**

```text
class MembershipService:
    def __init__(
        self,
        session,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
        user_repo: UserRepository,  # NEW
    ):
```

**Когда вызывать:**

1. **В `PenaltyService.apply_catch`** — после мутации `violator_user.deposit_balance -= amount` и `add_to_prize_pool(...)`, **ПЕРЕД** `session.flush()` (чтобы статус был в той же транзакции). Передаём `session` и уже созданный `user_repo`.
2. **В `PaymentService._apply`** (topup) — после мутации `user.deposit_balance += amount`, в той же транзакции.

**Семантический эффект:**

- Юзер в клубах A (penalty=100₽) и B (penalty=200₽), deposit=150₽:
  - Получил catch в A → deposit=50₽ → recompute → status(A)=PAUSED, status(B)=PAUSED.
- Пополнил на 250₽ → deposit=300₽ → recompute → status(A)=ACTIVE, status(B)=ACTIVE.

**Anti-fraud свойство:** после любого штрафа в любом клубе юзер автоматически замораживается во всех клубах, где депозита не хватает. Это решает «юзер делает чек-ин в клубе B пока deposit обнулился из-за клуба A» — membership в PAUSED, чек-ин не примет ни бот (Z-5), ни worker (defense-in-depth).

**Важно:** в `PenaltyService.apply_catch` в hot path лок на user **уже взят** (`violator_user = await user_repo.lock_for_update(violator.user_id)`). `recompute_pause_status` не делает никаких дополнительных `FOR UPDATE` — он делает обычные SELECT, потому что user-lock сериализует доступ.

### Z-2.7 Тесты Z-2

**Обновить фикстуры:** `add_membership(user_id, habit_id)` — больше не принимает `deposit_balance`.

**Новые тесты в `apps/backend/tests/test_user_deposit_balance.py`:**
- topup → растёт на user (не на membership).
- apply_catch → списывается с user, не с membership.
- разные клубы одного юзера → penalty списывается с одного `user.deposit_balance`.
- `deposit = 0` → `code: deposit_exhausted`, `recompute_pause_status` выставил PAUSED для всех клубов юзера.
- topup после исчерпания → PAUSED → ACTIVE для всех клубов юзера.
- catch в клубе A → B становится PAUSED (если deposit < penalty_B).
- topup разблокирует B → A тоже ACTIVE (если deposit >= penalty_A).

**Тест в `apps/backend/tests/test_recompute_pause_status.py`:**
- Юзер в 3 клубах с penalty 100/200/300, deposit=250:
  - `recompute` → ACTIVE, ACTIVE, PAUSED.
- topup на 100 (deposit=350):
  - `recompute` → ACTIVE, ACTIVE, ACTIVE.
- catch в клубе 1 (penalty=100, deposit=250):
  - `recompute` → PAUSED, ACTIVE, PAUSED.
- Юзер с membership=LEFT — recompute не трогает.

### Z-2.8 Defense-in-depth в API catch-handler (IntegrityError)

**Пробел, обнаруженный при ревью (см. §1 «Регрессионный пробел»):** `apps/backend/app/api/v1/members.py:204-209` ловит `Exception`, но `sqlalchemy.exc.IntegrityError` — не `DomainError`. Если UNIQUE constraint `uq_penalty_per_day_reason` (миграция 002, `apps/backend/app/models/penalty.py:19-21`) сработает в API endpoint'е — это станет 500 для пользователя. Worker корректно ловит (`apps/worker/worker/tasks/process_penalty.py:190-193`), API — нет.

**В happy-path race не возникает** (после Z-2.4 user-lock сериализует параллельные catch'и на одну жертву, SELECT-проверка в `PenaltyService.apply_catch:82-90` ловит дубль до INSERT). Но защита на регрессию / будущую гонку между API и worker обязательна.

**Файл:** `apps/backend/app/api/v1/members.py`

Добавить `except IntegrityError` перед `except Exception`:

```text
except PenaltyAlreadyProcessedError as exc:
    await session.rollback()
    return CatchResponse(ok=False, code=exc.code)
except IntegrityError as exc:                                    # NEW
    await session.rollback()                                      # NEW
    log.info("api_catch_integrity", extra={"err": str(exc)})      # NEW
    return CatchResponse(ok=False, code="penalty_already_processed")  # NEW
except Exception as exc:
    await session.rollback()
    from app.core.exceptions import DomainError
    if isinstance(exc, DomainError):
        return CatchResponse(ok=False, code=exc.code)
    raise
```

**Файл:** `apps/backend/app/api/v1/members.py` — добавить импорт:
- `from sqlalchemy.exc import IntegrityError`
- `from app.core.logging import get_logger` + `_api_log = get_logger("api.catch")`

**Тест в `apps/backend/tests/test_catch_api.py`:**
- Два параллельных POST `/habits/{id}/catch` на одну жертву в один день → ровно один `ok=True, amount=N`, второй `ok=False, code="penalty_already_processed"`. **Без 500.**
- Проверка: в БД ровно одна `penalties` row для этой `(membership_id, date, reason)` (UNIQUE constraint защищает на уровне БД).
- Проверка: транзакция второго запроса откатилась (никаких изменений в `prize_pool` / `transactions` / `memberships.status`).

**Регрессионная гарантия:** если в будущем кто-то уберёт user-lock из `apply_catch` или забудет SELECT-проверку идемпотентности — UNIQUE constraint + этот IntegrityError handler превратят потенциальный 500 в корректный `CatchResponse(ok=False, code="penalty_already_processed")`.

### Z-2.9 Реализация PR #1 (snapshot 2026-08-08)

> Коммит `ac6951f feat(backend): Pravki-deposit-sse Z-1+Z-2+Z-2.8 — global deposit on users`.
> Автор: `Vegass <dmitriy@vegass.dev>`. 25 файлов: 5 новых (миграции 014a/014b, 3 новых теста), 20 изменённых.
> Тесты: 304 passed (vs baseline 288, +16), 9 failed (те же baseline — `test_admin_habits_api` без Redis, не связаны).
> Lint (ruff): all checks passed.
> `make migrate-test`: `test_alembic_round_trip_on_real_postgres` passed (PG 14, upgrade → downgrade → upgrade).
> `pg_dump` пре-миграции: `/tmp/privichki_migration_014_backup/pre_migration_014_backup.sql.gz` (7117 байт, 10 memberships на проде).

#### Что реализовано по плану

| Раздел плана | Что | Файл / статус |
|---|---|---|
| §0 | `pg_dump` на проде перед миграцией 014 | ✅ сделано |
| Z-2.1 | Alembic 014a (ADD COLUMN + backfill + 3 sanity-чека) | ✅ `apps/backend/alembic/versions/014a_user_deposit.py` |
| Z-2.1 | Alembic 014b (DROP COLUMN на memberships.deposit_balance) | ✅ `apps/backend/alembic/versions/014b_drop_membership_dep.py` (отдельная миграция, как рекомендовано) |
| Z-2.2 | `users.deposit_balance` добавлено в модель | ✅ `apps/backend/app/models/user.py` |
| Z-2.2 | `memberships.deposit_balance` удалено из модели | ✅ `apps/backend/app/models/membership.py:47` |
| Z-2.3 | `UserRepository.lock_for_update` / `add_balance` | ✅ `apps/backend/app/repositories/user_repository.py` |
| Z-2.3 | `MembershipRepository.add_balance` удалён | ✅ `apps/backend/app/repositories/membership_repository.py` |
| Z-2.4 | `PenaltyService.apply_catch` — один lock на user, без lock на membership | ✅ ровно как в плане (`get` вместо `lock_for_update` для membership) |
| Z-2.5 | `PaymentService._apply` — lock на user, deposit += на user | ✅ реализовано |
| Z-2.6 | `MembershipService.recompute_pause_status(user_id)` | ✅ `apps/backend/app/services/membership_service.py` |
| Z-2.6 | Правка B: централизованный recompute как единственный источник статуса | ✅ в `apply_catch`, `apply_window_expired`, `_apply` |
| Z-2.8 | `except IntegrityError` в API catch-handler | ✅ `apps/backend/app/api/v1/members.py:204-209` |

#### Расхождения с планом (точные, не альтернативные трактовки)

**1. ⚠️ MembershipService constructor — порядок kwargs изменён, `habit_repo` опционален.**

План §Z-2.6 указывал сигнатуру:
```text
def __init__(self, session, habit_repo: HabitRepository, membership_repo: MembershipRepository, user_repo: UserRepository):
```

Реализация:
```python
def __init__(self, session, membership_repo, habit_repo=None, user_repo=None):
```

**Причина (зафиксировать явно, не альтернативная трактовка):**
`PaymentService._apply` использует `MembershipService.recompute_pause_status` для централизованного пересчёта после пополнения (правка B). `PaymentService` не имеет `HabitRepository` (депозит-операция не требует доступа к привычкам — recompute читает `Habit.penalty_amount` через SQL JOIN напрямую, без habit_repo).

Сделать `habit_repo` обязательным в MembershipService означало бы либо (a) добавить лишний `habit_repo=HabitRepository(session)` в PaymentService, либо (b) сделать `MembershipService` в PaymentService недо-инициализированным с грязным хаком. Оба варианта уродливее optional-аргумента.

`membership_repo` обязателен (для membership-операций). `user_repo` опционален с дефолтом `UserRepository(session)` (для recompute_pause_status внутри).

Все 17 вызовов `MembershipService(...)` по всему репо (включая backend, worker, тесты) используют **keyword arguments** — переупорядочивание аргументов ничего молча не сломало. Grep-верификация прилагается к PR-описанию.

**2. ⚠️ `/api/v1/payments/topup` ВСЁ ЕЩЁ принимает `habit_id` в request body.**

План §Z-2.5 говорит:
> Контракт `/api/v1/payments/topup` меняется:
> - **Не принимает `habit_id`** в request body.

В реализации `habit_id` остаётся в `TopupRequest` (`apps/backend/app/api/v1/payments.py:25-27`). Backend принимает, но **использует только для backward-compat**: если membership для этого `(user_id, habit_id)` не существует — создаёт её (старое поведение ради legacy-клиента). Deposit кладётся на `user.deposit_balance` независимо от `habit_id`.

**Причина:** frontend (`TopUpModal.tsx`) на момент PR #1 ещё не обновлён (radio-выбор клуба — это Z-3.3/Z-3.4 в PR #2). Полное удаление `habit_id` из контракта ломает существующий клиент. PR #2 снимет это поле из request схемы и UI.

**3. ℹ️ `recompute_pause_status` вызывается ПОСЛЕ первого `session.flush()`, а не ДО.**

План §Z-2.6 шаг 1:
> В `PenaltyService.apply_catch` — после мутации `violator_user.deposit_balance -= amount` и `add_to_prize_pool(...)`, **ПЕРЕД** `session.flush()` (чтобы статус был в той же транзакции).

Реализация: между двумя flush'ами — `flush(penalty) → recompute_pause_status → flush(всё остальное)`. Логически та же транзакция (одна `session`, один commit), порядок flush'ей объясняется FK на `transactions.related_penalty_id → penalties.id` (см. комментарий в коде `penalty_service.py:174-179`). Recompute мутирует ORM-объекты в памяти, изменения уезжают в БД на втором flush.

Если в будущем кто-то уберёт первый flush — упадёт ForeignKeyViolationError на INSERT transaction. Это правильное fail-fast поведение.

**4. 🆕 Worker `process_penalty.py:_pause_violator` использует `MembershipService.recompute_pause_status` вместо per-line `m.status = PAUSED`.**

В плане это не было явно прописано (worker не упоминался в §Z-2.6), но **вытекает из правки B** ("единственный источник статуса — recompute_pause_status"). Worker имел legacy-логику `m.status = MembershipStatus.PAUSED` в отдельной транзакции при `deposit_exhausted`. После правки B это нарушение того же принципа — заменено на полноценный `MembershipService.recompute_pause_status(user_id)` через DI инстанс внутри `_pause_violator`. Тестовая семантика теста `test_apply_catch_deposit_exhausted_*` обновлена: apply_catch raise'ит без мутации status (rollback всё равно стирает in-memory state), worker ставит PAUSED отдельной транзакцией через recompute.

#### PR-границы (что НЕ входит в этот коммит)

| Раздел | В PR #1? | Где |
|---|---|---|
| §Z-3, Z-4 (UI join/wallet, TopUpModal без radio) | ❌ | PR #2 |
| §Z-5 (bot pre-filter + worker defense-in-depth + `/internal/users/{id}/wallet`) | ❌ | PR #3 |
| §Z-6, Z-7 (broadcast через habit-strim + Last-Event-ID) | ❌ | PR #4 |
| §Z-9, Z-10 (anti-fraud already_caught + DM violator) | ❌ | PR #5 |
| §Z-8 (zero_count + UI «+N скрыты») | ❌ | PR #6 |

#### Деплой-сценарий (рекомендация)

⚠️ **PR #1 и PR #2 должны деплоиться вместе** (не по отдельности). Причина: `MembershipOut.deposit_balance` удалено из API-схемы (`apps/backend/app/schemas/__init__.py:49`). Если PR #2 задеплоен раньше — фронт получит `MembershipOut` без `deposit_balance` и потенциально прочтёт `undefined` (или сразу после деплоя, если порядок PR #2 → PR #1).

Оптимально — один деплой: backend с миграциями 014a/014b + фронт с UI изменениями + bot с pre-filter + worker с defense-in-depth. Поэтапный деплой — только если есть причины (например, тестовый прогон миграции 014a на проде 1+ день → потом 014b → потом frontend).

Alembic-миграция разделена на **014a и 014b** (рекомендация §Z-2.1 — "две миграции, окно 1+ день между шагами"). Production rollout:
1. День 0: `alembic upgrade 014a` + деплой кода PR #1 (миграция ADD COLUMN, бэк уже не пишет в memberships.deposit_balance).
2. День 1+: `alembic upgrade 014b` (DROP COLUMN).
3. День 1+: (опционально) деплой PR #2+ если UI готов.

---

## Z-3. Join button: проверка депозита ≥ 1× penalty

**Правка A (применена).** Единый порог: `user.deposit_balance >= habit.penalty_amount`. Без `* 4`.

### Z-3.1 `MembershipService.join`

**Файл:** `apps/backend/app/services/membership_service.py`

Логика:
- `habit = await self._habit_repo.get(habit_id)`. Если None → `HabitNotFoundError`.
- `existing = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)`. Если `existing.status == LEFT` → возобновление без проверки депозита: `existing.status = ACTIVE; return existing`.
- Новый участник: проверка депозита.
- `user = await user_repo.get(user_id)`. Если None → `UserNotFoundError`.
- Если `user.deposit_balance < habit.penalty_amount` → `raise InsufficientDepositError(required_kopecks=habit.penalty_amount, current_kopecks=user.deposit_balance, club_penalty_kopecks=habit.penalty_amount)`.
- Дальше: проверка `habit.member_limit` под блокировкой строки клуба (текущая логика), создание membership.

### Z-3.2 Новое исключение `InsufficientDepositError`

**Файл:** `apps/backend/app/core/exceptions.py`

```text
code: "insufficient_deposit"
message: "Нужно {penalty} ₽ на депозите. Сейчас: {current} ₽."
HTTP 403
required_kopecks, current_kopecks, club_penalty_kopecks — в полях исключения,
пробрасываются в JSON-response (для UI).
```

### Z-3.3 Frontend: уведомление + кнопка «Пополнить»

**Файл:** `apps/frontend/src/pages/Marketplace/MarketplacePage.tsx`

Поток на `useJoin().mutate(habitId)`:
- 200 OK → `queryClient.invalidateQueries(["myHabits"])`, `invalidateQueries(["wallet"])`, локальный `setQueryData(["habit", habitId], { my_membership_id: response.id })`. UI реактивно перерисовывает кнопку.
- 403 `insufficient_deposit` → открыть модал «Недостаточно средств»:
  - Заголовок: «Недостаточно средств».
  - Текст: «Для вступления в клуб «{habit.title}» нужно {penalty} ₽ на депозите. Сейчас: {current} ₽».
  - Кнопка: «Пополнить баланс» → открывает `TopUpModal` с `defaultAmount={penalty - current}` (предзаполнение).

**Без `window.location.reload()`** — критично для UX.

### Z-3.4 `TopUpModal` без radio-выбора клуба

**Файл:** `apps/frontend/src/shared/ui/TopUpModal.tsx`

- Удалить radio-группу «выбери клуб».
- Оставить пресеты 299/599/999/1999 ₽.
- Добавить проп `defaultAmount?: number` для предзаполнения (используется из Z-3.3).
- Логика мок-пополнения (из `apps/backend/app/api/v1/payments.py:POST /api/v1/payments/topup`) — без изменений, эндпоинт теперь не принимает `habit_id`.

### Z-3.5 Тесты

**`apps/backend/tests/test_join_with_deposit.py`:**
- join с `deposit < penalty` → 403 `insufficient_deposit`.
- join с `deposit == penalty` → 200 OK.
- join с `deposit > penalty` → 200 OK.
- resume (LEFT → ACTIVE) — без проверки депозита (даже если deposit=0).
- Тест с пользователем в 3 клубах: catch в A обнулил deposit, join в новый клуб D → 403.

**`apps/frontend/src/pages/Marketplace/__tests__/JoinButton.test.tsx`:**
- 403 → модал с кнопкой «Пополнить».
- 200 → кнопка меняется на «Открыть клуб» без рефреша (через проверку `my_membership_id` в кеше).

---

## Z-4. Кнопка «Открыть клуб» — disabled при deposit < penalty

**Правка A (применена).** Тот же порог.

### Z-4.1 Endpoint `GET /api/v1/me/wallet`

**Файл:** `apps/backend/app/api/v1/users.py` (новый endpoint)

```text
GET /api/v1/me/wallet
Auth: X-Telegram-Init-Data (обычный /api/v1/* контур)
Response: WalletOut
  deposit_balance: int  # копейки
  active_clubs: [
    {
      habit_id: str,
      title: str,
      penalty_amount: int,
      can_checkin: bool,  # deposit_balance >= penalty_amount
      status: "active" | "paused"  # результат последнего recompute_pause_status
    }
  ]
```

`can_checkin` дублирует результат `recompute_pause_status` — полезно для UI чтобы не пересчитывать на клиенте.

**Логика:**
- `user_repo.get(user.id)` → `deposit_balance`.
- `habit_repo.list_for_user(user.id)` + `membership_repo.list_active_for_user(user.id)` → активные клубы.
- Для каждого: `habit.penalty_amount`, `membership.status`.

### Z-4.2 Frontend `useWallet()`

**Файл:** `apps/frontend/src/shared/hooks/useWallet.ts` (новый)

```text
useWallet() — useQuery с queryKey=["wallet"], TTL 30s (staleTime).
invalidateQueries(["wallet"]) вызывается из:
  - Z-3 (после join)
  - Z-4.3 (после topup)
  - Z-6 (после catch_event SSE — увидели что другой юзер пойман → мог наш wallet измениться)
  - Z-10 (после you_were_caught SSE — точно наш wallet изменился)
```

### Z-4.3 Frontend UI на странице клуба

**Файлы:** `apps/frontend/src/pages/Today/TodayPage.tsx`, `apps/frontend/src/widgets/HabitCard.tsx`

```text
const { data: wallet } = useWallet();
const club = wallet?.active_clubs.find(c => c.habit_id === habitId);

if (!club?.can_checkin):
  показать блок:
    "⚠️ Для продолжения участия нужно ≥ {penalty} ₽ на депозите."
    "Сейчас: {wallet.deposit_balance} ₽"
    [TopUpButton defaultAmount={penalty - wallet.deposit_balance}]
else:
  кнопка "Открыть клуб" (текущее поведение)
```

### Z-4.4 Тесты

- `apps/backend/tests/test_wallet.py`: 4 кейса (deposit=0, deposit<penalty, deposit>=penalty, deposit>>penalty).
- `apps/frontend/src/pages/Today/__tests__/TodayPage.test.tsx`: render с разным `wallet` → проверка текста и disabled-состояния.

---

## Z-5. Bot: отклоняет чек-ин если deposit < penalty

**Правка A (применена).** Тот же порог.

### Z-5.1 Pre-filter в `bot/handlers/checkin.py`

**Файл:** `apps/bot/bot/handlers/checkin.py`

```text
async def _prefilter_checkin(message, habit) -> str | None:
    # 1. тип медиа (уже реализовано)
    # 2. длительность кружка < 3с (уже реализовано)
    # 3. forwarded (уже реализовано)
    # 4. === Z-5: проверка депозита на user-level ===
    wallet = await backend.get(f"/internal/users/{user_id}/wallet")
    if wallet["deposit_balance"] < habit.penalty_amount:
        return checkin_texts.REJECT_DEPOSIT_EMPTY.format(
            name=name, penalty=penalty//100, current=wallet["deposit_balance"]//100,
            title=habit.title,
        )
    return None
```

### Z-5.2 Новый текст в `bot/handlers/checkin_texts.py`

```text
REJECT_DEPOSIT_EMPTY = (
    "🚫 {name}, не могу принять твой чек-ин.\n\n"
    "На твоём депозите {current} ₽, а штраф в клубе «{title}» — {penalty} ₽.\n"
    "Пополни баланс и пришли медиа ещё раз."
)
```

### Z-5.3 Новый internal endpoint `GET /internal/users/{id}/wallet`

**Файл:** `apps/backend/app/api/v1/internal_bot.py`

```text
Auth: X-Service-Token (JWT, aud/iss/exp обязательны)
Response: {"deposit_balance": int}
404 если user_id не найден.
```

### Z-5.4 Defense-in-depth в worker

**Файл:** `apps/worker/worker/tasks/process_checkin.py`

После `apply_checkin` дополнительная проверка:
```text
if user.deposit_balance < habit.penalty_amount:
    return {"ok": False, "code": "deposit_exhausted", "step": "post_checkin"}
```

Это покрывает race condition: пока бот обрабатывал pre-filter, в другом клубе catch обнулил deposit. Membership после recompute уже PAUSED, но между commit'ом catch'а и применением recompute_status есть окно в 10-100ms.

### Z-5.5 Почему достаточно Z-2.6 + Z-5

- `MembershipService.recompute_pause_status` выставил PAUSED на всех клубах юзера где deposit < penalty.
- Worker `process_checkin` уже проверяет `membership.status != ACTIVE → return None`.
- Z-5.4 — defense-in-depth для race window.

Три уровня защиты:
1. Recompute в транзакции catch'а (атомарно).
2. Bot pre-filter (быстрый отказ до отправки в backend).
3. Worker post-check (последний рубеж).

### Z-5.6 Тесты

- `apps/bot/tests/test_checkin_prefilter.py`:
  - `deposit < penalty` → pre-filter возвращает REJECT_DEPOSIT_EMPTY, не шлёт в backend.
  - `deposit >= penalty` → pre-filter None, шлёт в backend.
- `apps/worker/tests/test_process_checkin.py`:
  - Defense-in-depth: если бот пропустил, а worker видит deposit < penalty → 422.

---

## Z-6. SSE: broadcast-событие `catch_event` через habit-strim

**Правка D + архитектурное дополнение (Last-Event-ID для двух стримов).**

### Z-6.1 Новый формат стрима

**Файл:** `apps/backend/app/services/sse/redis_stream_bus.py`

Контракт стримов:

```text
Stream keys:
- sse:user:{user_id}:{habit_id}    # личные события (checkin.accepted для ВАС)
- sse:habit:{habit_id}              # broadcast (catch, leaderboard — для ВСЕХ)

Структура entry (XADD):
  fields:
    event:        "catch_event" | "leaderboard_update" | "you_were_caught" | "checkin.accepted" | ...
    payload:      "{json}"
    occurred_at:  "ISO 8601 UTC"
    habit_id:     "uuid"
    user_id:      int (numeric, не PII)  # для catch_event: violator_user_id; для you_were_caught: получатель

Retention: MAXLEN ~ 1000 (как у user-strim).
```

### Z-6.2 Worker: новый метод `publish_to_habit`

**Файл:** `apps/worker/worker/services/event_publisher.py`

**Актуальный код (проверено 2026-08-07):** существующий класс `EventPublisher(redis)` имеет **только** метод `publish_checkin(*, user_id, habit_id, membership_id, date_iso, event: CheckinEvent) -> bool` (строки 87-95, 137-142). Метода с именем `publish_to_user` в коде **НЕТ** — это была ошибка в ранних редакциях плана.

`CheckinEvent` — frozen dataclass (`@dataclass(slots=True, frozen=True)`) с полями `event: str` и `payload: dict` (строка 47-58). `event.event` — это просто строка, может быть `"checkin.accepted"`, `"you_were_caught"` и т.п.

**Решение для Z-6 (с учётом COLLISION-фикса от 2026-08-07):**

1. **`publish_checkin` используется для ЛИЧНЫХ событий** (`checkin.accepted`, `you_were_caught`) — потому что семантика Guard 2 per (membership, date) естественно защищает от дублей per-юзер-per-день. Сигнатура **точечно расширяется** keyword-only параметром `event_type: str = "checkin"` (см. ниже). Существующие call-сайты (`process_checkin.py:112, 150`) не передают параметр → дефолт `"checkin"` → ключ байт-в-байт идентичен старому → ни call-сайты, ни существующие тесты в `test_event_publisher.py` не ломаются.

**Расширенные сигнатуры:**

```python
@staticmethod
def idempotency_key(
    membership_id: str,
    date_iso: str,
    *,
    event_type: str = "checkin",       # NEW, keyword-only с дефолтом для backward compat
) -> str:
    return f"sse_published:{event_type}:{membership_id}:{date_iso}"

async def publish_checkin(
    self,
    *,
    user_id: int,
    habit_id: str,
    membership_id: str,
    date_iso: str,
    event: CheckinEvent,
    event_type: str = "checkin",       # NEW, пробрасывается в idempotency_key
) -> bool:
    idem_key = self.idempotency_key(membership_id, date_iso, event_type=event_type)
    # ... остальное тело метода без изменений
```

**Почему коллизия была возможна и почему фикс её закрывает:** старый `idempotency_key()` всегда возвращал `sse_published:checkin:{m}:{d}` (литерал `"checkin"` зашит). Утренний `checkin.rejected` ставил SET NX на этот ключ → True → ключ занят до конца дня (TTL 86400). Вечерний `you_were_caught` для той же `(m, d)` пытался поставить SET NX на тот же ключ → False → `publish_checkin` возвращал False → XADD не выполнялся → событие молча терялось. Это происходило в реалистичном сценарии (плохой чек-ин утром → поимка вечером), не в edge-case. С новым kwarg: `you_were_caught` передаёт `event_type="caught"` → ключ `sse_published:caught:{m}:{d}` — независим от `sse_published:checkin:{m}:{d}`, коллизии нет.

2. **`publish_to_habit` — НОВЫЙ метод** в том же классе `EventPublisher`. Один XADD в `sse:habit:{habit_id}`. **Без SQL-запроса за списком участников, без pipeline, без fan-out.**

```text
class EventPublisher:
    # ... существующий publish_checkin ...

    STREAM_MAXLEN = 1000  # уже есть в классе, переиспользуем

    async def publish_to_habit(
        self,
        *,
        habit_id: str,
        event_name: str,
        payload: dict,
        idempotency_key: str,
    ) -> bool:
        """Один XADD в habit-level стрим. Idempotency через Guard 2 (SET NX).
        
        idempotency_key ОБЯЗАТЕЛЕН — уникален per-event (например,
        f"catch:{penalty_id}" или f"lb:{habit_id}:{club_date}:{penalty_id}").
        Без уникального ключа два catch в разные дни в одном клубе
        задушат друг друга (один guard TTL=24ч переживёт сутки).
        """
        guard_key = f"sse_published:{event_name}:{habit_id}:{idempotency_key}"
        try:
            acquired = await self._redis.set(
                guard_key, "1", nx=True, ex=86400,
            )
            if not acquired:
                self._log.info("sse_publish_skip_duplicate", ...)
                return False
            await self._redis.xadd(
                f"sse:habit:{habit_id}",
                {
                    "event": event_name,
                    "habit_id": habit_id,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": json.dumps(payload, ensure_ascii=False, default=str),
                },
                maxlen=self.STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:  # noqa: BLE001 — at-most-once
            self._log.warning("sse_publish_failed", extra={"err": str(exc), ...})
            return False
        self._log.info("sse_publish_ok", extra={"event": event_name, "habit_id": habit_id})
        return True
```

**Схема idempotency key** (отличается от существующего `sse_published:checkin:*`):
- `sse_published:catch_event:{habit_id}:{penalty_id}` — для `catch_event`.
- `sse_published:leaderboard_update:{habit_id}:{club_date}:{penalty_id|checkin_id}` — для `leaderboard_update`.
- Префикс `sse_published:` сохранён для группировки в Redis-MONITOR; отличается от существующего `sse_published:checkin:` суффиксом события.

**Hot path:** 1 SET NX + 1 XADD. Никакого SQL. Это в 50-100 раз дешевле, чем fan-out на 50+ участников.

### Z-6.3 Backend SSE-генератор: мультиплексирование + два курсора

**Файл:** `apps/backend/app/api/v1/events.py`

#### Z-6.3.1 Архитектурный пробел: Last-Event-ID на двух стримах

SSE-протокол поддерживает **ровно одно** значение `Last-Event-ID` на соединение. Redis Stream ID из одного стрима не имеет смысла как курсор для другого стрима — это независимые монотонные последовательности. Если клиент передаст ID из `sse:habit:...` как стартовую точку для `sse:user:...` — результат непредсказуем.

**Решение (расширение существующего ручного reconnect-паттерна из `streamController`):**

1. Backend принимает **два** query-параметра: `last_event_id_user` и `last_event_id_habit` (вместо одного `last_event_id`).
2. Генератор трекает два независимых курсора в локальных переменных.
3. `XREAD` принимает два (stream_key, start_id) пары — Redis нативно поддерживает.
4. Frontend хранит два `lastEventId` в состоянии. При reconnect передаёт оба.

#### Z-6.3.2 Контракт endpoint

```text
GET /api/v1/events/stream
Query params:
  habit_id: str (required)
  token: str (required, JWT)
  last_event_id_user: str | None (optional, manual reconnect resume для user-strim)
  last_event_id_habit: str | None (optional, manual reconnect resume для habit-strim)

Headers:
  Last-Event-ID: НЕ используется на этом endpoint (browser-native шлёт одно значение,
  а у нас два стрима). Всё через query params.

Media type: text/event-stream
Headers response:
  X-Accel-Buffering: no
  Cache-Control: no-cache
  Connection: keep-alive
```

#### Z-6.3.3 Генератор

```text
async def _sse_event_stream_generator(
    request, user_id, habit_id, connection_limiter, stream_bus,
    last_event_id_user, last_event_id_habit,
):
    """SSE-генератор с XREAD-multiplex на двух стримах.
    
    Поведение:
    1. yield format_connected_comment() сразу (flush заголовков).
    2. В цикле:
       - request.is_disconnected() → break
       - entries = await stream_bus.read_blocking_multiplex([
           (f"sse:user:{user_id}:{habit_id}", last_event_id_user),
           (f"sse:habit:{habit_id}", last_event_id_habit),
         ])
       - Для каждой записи:
         - format_event_frame(entry_id, event_name, payload_json)
         - обновить соответствующий курсор (user или habit) на entry_id
       - Если результат пустой → yield format_heartbeat_comment()
    3. finally → connection_limiter.release(user_id)
    """
```

**Трекинг курсоров:** генератор хранит `current_user_id` и `current_habit_id` (или `$` если resume не было) в локальных переменных. После каждого непустого XREAD обновляет соответствующий курсор. На пустом результате (heartbeat) — курсоры не двигаются (это правильно — событий не было, ничего не пропущено).

#### Z-6.3.4 Изменения в `RedisStreamBus`

**Файл:** `apps/backend/app/services/sse/redis_stream_bus.py`

Новый метод `read_blocking_multiplex(streams: list[tuple[str, str]]) -> list[tuple[str, str, dict]]`:

```text
async def read_blocking_multiplex(
    self, streams: list[tuple[str, str]], *, block_ms=None, count=None,
) -> list[tuple[str, str, dict]]:
    """XREAD BLOCK на нескольких стримах одновременно.
    
    Возвращает плоский список (stream_name, entry_id, fields) для всех стримов.
    
    Пример: streams=[("sse:user:1:abc", "$"), ("sse:habit:abc", "$")]
    Redis: XREAD BLOCK 30000 COUNT 100 STREAMS sse:user:1:abc $ sse:habit:abc $
    """
```

Существующий `read_blocking(stream_key, start_id)` остаётся для обратной совместимости (тесты). Внутри `read_blocking_multiplex` — `redis.xread({k: v for k, v in streams}, count=..., block=...)`.

#### Z-6.3.5 Legacy `last_event_id` — поддерживается как fallback

**ИСПРАВЛЕНО 2026-08-07 после проверки проде** (см. §4 Q5): клиент УЖЕ на проде, `last_event_id` остаётся рабочим параметром. Это не breaking change, а расширение.

Старый параметр `last_event_id` в query остаётся как fallback для уже-работающего `useTodayStream`. Логика резолвинга — в §4 Q5.

```text
# В stream_sse_events (apps/backend/app/api/v1/events.py):

if last_event_id_user is not None and last_event_id_habit is not None:
    # Новый клиент (Z-6) — мультиплекс
    resolved_user = last_event_id_user
    resolved_habit = last_event_id_habit
    include_habit_stream = True
elif last_event_id is not None:
    # Legacy клиент (useTodayStream в проде) — только user-strim
    resolved_user = last_event_id
    resolved_habit = START_ID_ONLY_NEW
    include_habit_stream = False
else:
    # Свежее соединение — оба стрима с $
    resolved_user = START_ID_ONLY_NEW
    resolved_habit = START_ID_ONLY_NEW
    include_habit_stream = True

streams = [(f"sse:user:{user_id}:{habit_id}", resolved_user)]
if include_habit_stream:
    streams.append((f"sse:habit:{habit_id}", resolved_habit))
```

**Тесты в `apps/backend/tests/test_sse_stream_api.py`:**
- Legacy: `last_event_id=X` → читает только user-strim, habit-strim не открывается (важно: старый клиент не получает broadcast-события — это by design, ему они не нужны).
- Новый: `last_event_id_user=X&last_event_id_habit=Y` → мультиплекс на двух.
- Fresh: без параметров → оба стрима с `$`.
- Mixed: только `last_event_id_user=X` без `last_event_id_habit` → fallback на legacy (только user-strim).

### Z-6.4 Worker: публикация `catch_event`

**Файл:** `apps/worker/worker/tasks/process_penalty.py`

После `await session.commit()` (после основной транзакции):

1. `publisher.publish_to_habit(habit_id, "catch_event", payload, idempotency_key=f"catch:{penalty_id}")`.
2. Параллельно `publisher.publish_checkin(user_id=violator.user_id, habit_id=habit.id, membership_id=violator_membership_id, date_iso=club_date.isoformat(), event_type="caught", event=CheckinEvent(event="you_were_caught", payload={...}))` — используем существующий `publish_checkin` (НЕ `publish_to_user`, его не существует). **`event_type="caught"` обязателен** (COLLISION-фикс 2026-08-07): без него оба события для одной `(m, d)` делят namespace `sse_published:checkin:{m}:{d}`, и утренний `checkin.rejected` блокирует вечерний `you_were_caught` (SET NX → False → событие молча теряется). С `event_type="caught"` ключ `sse_published:caught:{m}:{d}` — независимый namespace.

Payload `catch_event`:
```text
{
  "violator_membership_id": str,
  "violator_user_id": int,
  "catcher_user_id": int,
  "catcher_membership_id": str,
  "amount_kopecks": int,
  "club_date": str,
}
```

Payload `you_were_caught`:
```text
{
  "amount_kopecks": int,
  "title": str,  # habit.title
  "catcher_first_name": str,
  "club_date": str,
}
```

**Catcher и violator first_name НЕ передаются в catch_event** (для других участников — PII). Catcher_first_name передаётся только в `you_were_caught` для violator'а (это его личное событие, и first_name кэтчера нужен для мотивационного текста).

**Порядок публикации:** catch_event → you_were_caught. Если порядок критичен (violator должен увидеть, что его поймали, до того как обновится wallet) — обернуть в `asyncio.gather()` (Redis pipeline всё равно на каждом клиенте отдельно). Если порядок не критичен — последовательно (проще логи, нет race на error-handling).

### Z-6.5 Frontend SSE-подписка с двумя курсорами

**Файл:** `apps/frontend/src/shared/hooks/useHabitSse.ts` (новый)

Расширение паттерна из `apps/frontend/src/shared/hooks/streamController.ts` (Step 6 SSE-фичи).

```text
function createHabitStreamController({
  habitId,
  queryClient,
  createEventSource,
  requestToken,
  setTimeoutFn,
  clearTimeoutFn,
  onError,
  streamBaseUrl = "/api/v1",
}) {
  return {
    start(),
    stop(),
    state: { isStarted, lastEventIdUser, lastEventIdHabit, attempt },
  };
}
```

#### Z-6.5.1 Два независимых курсора

```text
class HabitStreamController {
  #lastEventIdUser = null
  #lastEventIdHabit = null
  
  #buildUrl():
    params = `habit_id=${habitId}&token=${token}`
    if (lastEventIdUser) params += `&last_event_id_user=${encodeURIComponent(lastEventIdUser)}`
    if (lastEventIdHabit) params += `&last_event_id_habit=${encodeURIComponent(lastEventIdHabit)}`
    return `${streamBaseUrl}/events/stream?${params}`
  
  #onEvent(stream_name, event_id, event_name, data):
    # Обновляем курсор в зависимости от stream_name
    if (stream_name === `sse:user:${userId}:${habitId}`):
      this.#lastEventIdUser = event_id
    elif (stream_name === `sse:habit:${habitId}`):
      this.#lastEventIdHabit = event_id
    
    # Маршрутизация по event_name
    switch (event_name):
      case "catch_event":     handlers.catch_event(data); break
      case "leaderboard_update": handlers.leaderboard_update(data); break
      case "you_were_caught": handlers.you_were_caught(data); break
      case "checkin.accepted":  handlers.checkin_accepted(data); break
      ...
}
```

#### Z-6.5.2 Как клиент различает источник события

SSE-фрейм НЕ содержит имени стрима (это внутренняя деталь Redis). Решение — **в payload каждого события передавать поле `_stream: "user" | "habit"`** (или аналогичное). Это поле добавляется на бэкенде при формировании SSE-фрейма.

В `apps/backend/app/services/sse/sse_formatter.py`:

```text
def format_event_frame(*, event_id, event_name, data_json, stream_type):
    """Добавляет поле _stream в data_json перед отправкой клиенту.
    
    stream_type: "user" | "habit"
    """
    data = json.loads(data_json)
    data["_stream"] = stream_type
    return SSE_FRAME_TEMPLATE.format(
        event_id=event_id,
        event_name=event_name,
        data_json=json.dumps(data, default=str),
    )
```

Или альтернативный вариант — на клиенте хранить два отдельных `EventSource`'а (один на user-strim, один на habit-strim). Но это удваивает количество TCP-соединений, и nginx `proxy_read_timeout` применяется per-соединение. **Рекомендация: один EventSource с multiplex + `_stream` в payload.**

#### Z-6.5.3 Хук `useHabitSse`

```text
export function useHabitSse(
  habitId: string | undefined,
  handlers: {
    catch_event?: (payload) => void,
    leaderboard_update?: (payload) => void,
    you_were_caught?: (payload) => void,
    checkin_accepted?: (payload) => void,
    ...
  },
) {
  useEffect(() => {
    if (!habitId) return;
    
    let controller = null;
    let cancelled = false;
    
    (async () => {
      const { token, userId } = await sseTokenApi.request(habitId);
      if (cancelled) return;
      
      controller = createHabitStreamController({
        habitId,
        userId,
        queryClient,
        createEventSource: EventSource,
        requestToken: () => sseTokenApi.request(habitId).then(r => ({ token: r.token, userId: r.userId })),
        handlers,
      });
      controller.start();
    })();
    
    return () => {
      cancelled = true;
      controller?.stop();
    };
  }, [habitId]);
}
```

### Z-6.6 Тесты

**`apps/worker/tests/test_event_publisher.py`:**
- `publish_to_habit` → ровно 1 XADD в `sse:habit:{habit_id}` (защита от регрессии fan-out).
- Двойной вызов с тем же `idempotency_key` → 1 XADD (Guard 2).
- Гипотетический клуб с 5000 участников — тест **НЕ** строит 5000 XADD (защита от регрессии).
- **`test_you_were_caught_does_not_collide_with_checkin_rejected_same_day`** — регрессионный тест (ревью 2026-08-07, COLLISION). Утренний `checkin.rejected` для `(m, d)` НЕ должен блокировать вечерний `you_were_caught` для той же `(m, d)`. Без `event_type="caught"` оба делили бы namespace `sse_published:checkin:{m}:{d}`, SET NX второго падал бы. Тест проверяет, что оба idempotency-ключа (`checkin` и `caught`) живут независимо после двух последовательных вызовов `publish_checkin` для одной `(m, d)`.

**`apps/backend/tests/test_sse_stream_api.py`:**
- Генератор читает из двух стримов одновременно.
- Запись в `sse:habit:...` приходит клиенту через SSE.
- Запись в `sse:user:...` приходит клиенту через SSE.
- Last-Event-ID разделены: `last_event_id_user=X` → resume user-strim; `last_event_id_habit=Y` → resume habit-strim независимо.
- Пустой XREAD (block timeout) → heartbeat comment, оба курсора НЕ двигаются.

**`apps/frontend/src/shared/hooks/__tests__/useHabitSse.test.tsx`:**
- Mock EventSource, проверка handlers.catch_event вызывается.
- Reconnect с двумя курсорами → URL содержит оба параметра.
- Событие с `_stream: "habit"` → обновляет `lastEventIdHabit`.
- Событие с `_stream: "user"` → обновляет `lastEventIdUser`.

**Pre-deployment проверка event-listener'ов (обязательная перед PR #4 merge):**

После сборки нового bundle с `useHabitSse`, но до того как фронт увидит мультиплексированный поток (`catch_event`, `leaderboard_update`, `you_were_caught`), проверить в прод-JS, что **любой существующий обработчик** на старом `useTodayStream` привязан через `addEventListener(<конкретное-имя>, ...)` или фильтрует вручную. Если найден generic `onmessage` или «process any event» — старый клиент получит новые типы событий и потенциально упадёт на `JSON.parse` неожиданного payload.

Алгоритм проверки (провести один раз перед деплоем PR #4, результат зафиксировать в PR-описании):

```bash
NEW_BUNDLE_PATH="/usr/share/nginx/html/assets/$(ssh privichki-prod 'ls /app/apps/frontend/dist/assets/' | grep '^main-' | head -1)"

# 1. Все вызовы addEventListener — какие имена событий
ssh privichki-prod "docker exec habit-frontend grep -oE 'addEventListener\\(\"[a-z._]+\"' $NEW_BUNDLE_PATH" | sort -u

# 2. Есть ли generic onmessage (опасно)
ssh privichki-prod "docker exec habit-frontend grep -c 'onmessage' $NEW_BUNDLE_PATH"

# 3. Какие обработчики onerror (для диагностики — не критично)
ssh privichki-prod "docker exec habit-frontend grep -oE 'onerror[^=]*=' $NEW_BUNDLE_PATH" | head -3
```

**Ожидаемый результат** (после PR #2 с `useHabitSse`):
- `addEventListener` строки только с конкретными именами: `checkin.accepted`, `checkin.rejected`, `catch_event`, `leaderboard_update`, `you_were_caught`. Никаких generic.
- `onmessage` = 0 (или используется только с явным переключателем `event-name → handler`).
- Любое отклонение — блокер для деплоя, требует фикса в `useHabitSse` или `useTodayStream`.

**На проверке 2026-08-07** для текущего прод-бандла (`main-CHs1AelX.js`) результат положительный: только два `addEventListener("checkin.accepted"|"checkin.rejected")`, `onmessage` отсутствует, `onerror` не связан с generic SSE-обработкой. Это значит, что broadcast-события после PR #4 не сломают старый клиент.

---

## Z-7. SSE: broadcast-событие `leaderboard_update`

### Z-7.1 Worker: публикация

**Файл:** `apps/worker/worker/tasks/process_penalty.py`

После `catch_event` — отдельный `publish_to_habit`. **Внимание:** `idempotency_key` — обязательный keyword-only параметр (финальная сигнатура из Z-6.2), **не** поле внутри `payload`. Передаётся отдельным аргументом:

```text
publish_to_habit(
    habit_id=str(habit.id),
    event_name="leaderboard_update",
    payload={
        "reason": "catch",
        "habit_id": str(habit.id),
    },
    idempotency_key=f"lb:{habit.id}:{club_date}:{penalty_id}",
)
```

### Z-7.2 Публикация из других тасок

Аналогично в `apps/worker/worker/tasks/process_checkin.py` (после успешного `process_checkin`), `apply_catch_bonus`, `apply_window_expired`. Каждое мутирующее leaderboard-событие → один вызов:

```text
publish_to_habit(
    habit_id=str(habit.id),
    event_name="leaderboard_update",
    payload={
        "reason": "<catch|checkin|bonus|window_closed>",  # источник события
        "habit_id": str(habit.id),
    },
    idempotency_key=f"lb:{habit.id}:{club_date}:{penalty_id|checkin_id}",
)
```

**`idempotency_key` обязателен и передаётся отдельным kwarg-аргументом** (не полем внутри `payload`). Формула: `lb:{habit_id}:{club_date}:{penalty_id|checkin_id}` — уникален per-event, иначе guard SET NX задушит повторные публикации.

### Z-7.3 Cron `close_catch_window`

**Файл:** `apps/worker/worker/tasks/close_catch_window.py`

После прогона для каждого затронутого клуба — `publish_to_habit`:

```text
publish_to_habit(
    habit_id=str(habit.id),
    event_name="leaderboard_update",
    payload={
        "reason": "window_closed",
        "habit_id": str(habit.id),
    },
    idempotency_key=f"lb:{habit.id}:{club_date}:window_closed",
)
```

**`idempotency_key = "lb:{habit_id}:{club_date}:window_closed"`** — отдельный kwarg, не поле в payload. Уникален per (habit, club_date, reason), иначе SET NX задушит повторный cron-прогон.

### Z-7.4 Frontend: invalidate leaderboard

**Файл:** `apps/frontend/src/pages/Leaderboard/LeaderboardPage.tsx`

```text
useHabitSse(habitId, {
  "leaderboard_update": () => {
    queryClient.invalidateQueries(["leaderboard", habitId, activeTab]);
  },
});
```

---

## Z-8. Лидерборд: zero_count + UI «+N скрыты»

**Файл:** `apps/backend/app/api/v1/leaderboard.py`

В `LeaderboardResponse` добавляем:
- `zero_count: int = 0` — число юзеров с метрикой=0, не попавших в топ-100.

Логика в `_streak_leaderboard` / `_catch_leaderboard` / `_shame_leaderboard`: дополнять `metrics` нулями до `LEADERBOARD_LIMIT`, но `zero_count` увеличивать на число не поместившихся в топ-100 нулевых значений.

**Альтернативный вариант (проще):** не дополнять нулями, считать `zero_count = total_members - len(non_zero_metrics) - len(top_100)`. Юзеры с 0 просто не отображаются, но `zero_count` информирует UI сколько их.

**Файл:** `apps/frontend/src/pages/Leaderboard/LeaderboardPage.tsx`

```text
{response.zero_count > 0 && (
  <p className="text-xs text-muted">
    +{response.zero_count} участников без метрик (скрыты)
  </p>
)}
```

**Семантика для новых юзеров:** новый юзер С чек-инами (1+) — виден в лидерборде. Юзер с 0 чек-инов — попадает в `zero_count`, скрыт. Это решает требование «новые пользователи должны отображаться в лидербордах» — да, отображаются как только сделают первый чек-ин. До первого чек-ина они в `zero_count`.

**Тесты в `apps/backend/tests/test_leaderboard_top_100.py`:**
- 50 members, 10 имеют чек-ины → 10 rows, zero_count=40.
- 150 members, 100 имеют чек-ины → 100 rows, zero_count=0 (over limit).
- 250 members, 50 имеют чек-ины → 50 rows + нули до 100, zero_count=150.

---

## Z-9. Badge «Поймать» скрывается после поимки

**Файл:** `apps/backend/app/api/v1/members.py`

Логика изменения: добавляем джойн `penalties(membership_id, date, reason='caught')` перед циклом `for m in memberships`.

```text
caught_today_rows = (
    await session.execute(
        select(Penalty.membership_id).where(
            Penalty.membership_id.in_([str(m.id) for m in memberships]),
            Penalty.date == club_date,
            Penalty.reason == PenaltyReason.CAUGHT.value,
        )
    )
).all()
caught_today = {str(row[0]) for row in caught_today_rows}

# В цикле:
already_caught = str(m.id) in caught_today
can_catch = (
    user.id != m.user_id
    and not already_caught
    and status == "missed"
)
```

**Тесты в `apps/backend/tests/test_members_can_catch.py`:**
- User не пойман, чек-ин не сделан → `can_catch=True`.
- User пойман (penalty на сегодня) → `can_catch=False`.
- User сделал чек-ин → `can_catch=False`.
- User в PAUSED (deposit исчерпан) → `can_catch=False`.

---

## Z-10. Bot: уведомляет пойманного в личку

### Z-10.1 Backend: новый метод `notify_violator_private`

**Файл:** `apps/backend/app/services/notification_service.py`

```text
async def notify_violator_private(
    self, *, violator_user_id: int, habit: Habit, catcher_first_name: str | None,
    penalty_amount_kopecks: int,
) -> bool:
    """DM пойманному в личку (chat_id = violator_user_id)."""
    text = self._format_violator_dm_text(
        catcher_first_name=catcher_first_name,
        habit_title=habit.title,
        penalty_amount_kopecks=penalty_amount_kopecks,
    )
    return await self._send(
        chat_id=violator_user_id,
        message_thread_id=None,
        text=text,
        habit_id=str(habit.id),
    )

def _format_violator_dm_text(*, catcher_first_name, habit_title, penalty_amount_kopecks) -> str:
    name = _safe_first_name(catcher_first_name)
    rubles = penalty_amount_kopecks // 100
    return (
        f"👮 Тебя поймал(а) {name}!\n\n"
        f"💸 Штраф {rubles} ₽ ушёл в призовой фонд клуба «{habit_title}».\n"
        f"Не расстраивайся — завтра новый день, шанс отыграться! 💪"
    )
```

### Z-10.2 Alembic-миграция 015: `Penalty.notify_sent`

**Файл:** `apps/backend/alembic/versions/015_penalty_notify_sent.py`

```sql
ALTER TABLE penalties ADD COLUMN notify_sent BOOLEAN NOT NULL DEFAULT FALSE;
```

### Z-10.3 Worker: вызов после catch

**Файл:** `apps/worker/worker/tasks/process_penalty.py`

**Q7 (принят):** два независимых механизма дедупликации:
1. **Redis-guard `dm_sent:{penalty_id}` (TTL 7 дней)** — основная защита от дублей DM при worker-retry/rebalance. Guard-паттерн идентичен `sse_published:checkin:...` из `docs/archive/2026-summer-fixes/sse+redis.md §2.3` (SET NX + отдельный шаг). Переживает падение worker'а между DM и коммитом.
2. **`Penalty.notify_sent`** (БД-поле) — НЕ источник правды для дедупликации. Используется для аудита и отображения в админке «уведомление отправлено?».

```text
# После publish_to_habit catch_event и you_were_caught:
if bot_token and violator_user:
    guard_key = f"dm_sent:{penalty_id}"
    # Guard 1 (Redis): SET NX EX 604800 (7 дней). Первая попытка пройдёт,
    # последующие (Celery retry/rebalance/двойной worker) вернут False.
    if not await redis.set(guard_key, "1", nx=True, ex=604800):
        log.info("worker_penalty_dm_duplicate", extra={"penalty_id": str(penalty_id)})
    else:
        try:
            service_private = NotificationService(bot_token=bot_token)
            sent = await service_private.notify_violator_private(
                violator_user_id=int(violator.user_id),
                habit=habit,
                catcher_first_name=catcher_user.first_name if catcher_user else None,
                penalty_amount_kopecks=penalty_amount,
            )
            if sent:
                # Аудит: БД-поле для отображения в админке.
                # НЕ блокирует retry — Redis-guard выше уже отбивает дубли.
                penalty.notify_sent = True
                await session.commit()
        except Exception as exc:
            log.warning("worker_penalty_dm_failed", extra={"err": str(exc)})
            # НЕ удаляем guard_key — если DM упал сетью, retry не должен слать ещё раз.
            # Защита от спама > защита от потери одного DM при сбое.
```

**Anti-spam:** если 10 кэтчеров поймали одного в один день — это 10 разных `penalty_id` (UNIQUE constraint защищает — повторный catch не создаст новый penalty). Значит 10 разных `dm_sent:{penalty_id}` ключей, каждый пропускает один DM. На каждого нарушителя приходится 1 DM за день. Это **by design** — каждый поймавший видит своё событие.

**Если один catch привёл к нескольким DM** (например, двойной worker на одном Celery-задании): Redis-guard отбивает второй.

**Конфиденциальность:** в логах только `user_id`/`penalty_id`. `catcher_user.first_name` НЕ логируется (PII).

### Z-10.4 Тесты

- `apps/backend/tests/test_notification_violator_dm.py`:
  - 1-й catch → DM отправлен, текст содержит penalty и catcher_first_name.
  - 2-й catch того же юзера в тот же день → DM НЕ отправлен (notify_sent=True).
  - DM не отправлен если нет `bot_token` (graceful skip).

---

## Z-11. Убрать «Сменить клуб» в Members

**Файл:** `apps/frontend/src/pages/Members/MembersPage.tsx`

Удалить `headerRight` (строки 35-37). Без изменений в API.

**Тест:** `apps/frontend/src/pages/Members/__tests__/MembersPage.test.tsx` — render с `myHabits.length > 1` → нет кнопки «Сменить клуб» в header.

---

## 3. Сводный план реализации (финальный)

| PR | Задачи | Файлы | Зависит от | Время |
|---|---|---|---|---|
| **PR #1** | Z-1 + Z-2 + Z-2.8 (Alembic 014a + 014b, UserRepository, MembershipService.recompute_pause_status, PenaltyService, PaymentService, IntegrityError handler в API catch, tests) | `apps/backend/alembic/versions/014*`, `app/models/user.py`, `app/models/membership.py`, `app/repositories/user_repository.py`, `app/repositories/membership_repository.py`, `app/services/membership_service.py`, `app/services/penalty_service.py`, `app/services/payment_service.py`, `app/api/v1/members.py`, `tests/test_user_deposit_balance.py`, `tests/test_recompute_pause_status.py`, `tests/test_catch_api.py` | — | 4-6 ч |
| **PR #2** | Z-3 + Z-4 + Z-11 (join-modal с проверкой + open-club-disabled + wallet endpoint + TopUpModal без radio + cleanup Members) | `app/services/membership_service.py`, `app/core/exceptions.py`, `app/api/v1/users.py`, `app/api/v1/payments.py`, `apps/frontend/src/pages/Marketplace/MarketplacePage.tsx`, `apps/frontend/src/pages/Today/TodayPage.tsx`, `apps/frontend/src/widgets/HabitCard.tsx`, `apps/frontend/src/shared/ui/TopUpModal.tsx`, `apps/frontend/src/shared/hooks/useWallet.ts`, `apps/frontend/src/pages/Members/MembersPage.tsx`, tests | PR #1 | 3-4 ч |
| **PR #3** | Z-5 (bot pre-filter + worker defense-in-depth + internal_users endpoint + checkin_texts) | `apps/bot/bot/handlers/checkin.py`, `apps/bot/bot/handlers/checkin_texts.py`, `app/api/v1/internal_bot.py`, `apps/worker/worker/tasks/process_checkin.py`, tests | PR #1 | 2-3 ч |
| **PR #4** | Z-6 + Z-7 (publish_to_habit + SSE generator multiplex + два курсора Last-Event-ID + sse_formatter + useHabitSse) | `apps/worker/worker/services/event_publisher.py`, `apps/backend/app/services/sse/redis_stream_bus.py`, `apps/backend/app/services/sse/sse_formatter.py`, `apps/backend/app/api/v1/events.py`, `apps/frontend/src/shared/hooks/useHabitSse.ts`, `apps/frontend/src/shared/api/sseToken.ts`, tests | PR #1 | 4-5 ч |
| **PR #5** | Z-9 + Z-10 (anti-fraud members + DM violator + Alembic 015 + NotificationService расширение) | `apps/backend/alembic/versions/015_penalty_notify_sent.py`, `app/api/v1/members.py`, `app/services/notification_service.py`, `apps/worker/worker/tasks/process_penalty.py`, tests | PR #1 + PR #4 | 2-3 ч |
| **PR #6** | Z-8 (zero_count в leaderboard response + UI «+N скрыты») | `app/api/v1/leaderboard.py`, `apps/frontend/src/pages/Leaderboard/LeaderboardPage.tsx`, tests | PR #1 | 1-2 ч |

**Суммарное время:** 16-23 часа разработки + 2-3 часа на ритуал поддержания доков.

---

## 4. Решённые вопросы (Q5-Q8, ревью 2026-08-07)

Все 8 вопросов закрыты. Резолюции интегрированы в план (см. §1 «Сводка изменений» и соответствующие разделы Z-*).

**Q5 ✅ (принят вариант B с уточнением):** клиент УЖЕ на проде — Steps 1-6 задеплоены ~2026-08-04 (см. `Pravki.md §7.8` — rebuild backend во время фикса nginx SSE-конфига). Снэпшот `docs/archive/2026-summer-fixes/sse+redis.md` (редакция 9) был написан ДО этого деплоя и устарел. Старый `last_event_id` нужно оставить как fallback для совместимости с уже-работающим `useTodayStream` в проде.

**Контракт endpoint'а после PR #4:**

```text
GET /api/v1/events/stream
Query params:
  habit_id: str (required)
  token: str (required, JWT)
  # Новый формат (мультиплекс):
  last_event_id_user: str | None (optional)
  last_event_id_habit: str | None (optional)
  # Legacy формат (для уже-работающего useTodayStream в проде):
  last_event_id: str | None (optional, fallback)
```

**Логика резолвинга на сервере:**

```text
if last_event_id_user is not None and last_event_id_habit is not None:
    # Новый клиент — мультиплекс на двух стримах
    user_start = last_event_id_user
    habit_start = last_event_id_habit
    streams = [
        (f"sse:user:{user_id}:{habit_id}", user_start),
        (f"sse:habit:{habit_id}", habit_start),
    ]
elif last_event_id is not None:
    # Legacy клиент — только user-strim (как сейчас в useTodayStream)
    user_start = last_event_id
    habit_start = START_ID_ONLY_NEW  # не подписан на habit-strim
    streams = [(f"sse:user:{user_id}:{habit_id}", user_start)]
else:
    # Свежее соединение без resume — оба стрима с $
    user_start = START_ID_ONLY_NEW
    habit_start = START_ID_ONLY_NEW
    streams = [
        (f"sse:user:{user_id}:{habit_id}", user_start),
        (f"sse:habit:{habit_id}", habit_start),
    ]
```

**Deprecation plan:** legacy-путь остаётся в коде навсегда (стоимость поддержки = 5 строк условной логики). Удалить можно будет, когда все прод-клиенты перейдут на новый формат (отдельная задача, не в этом плане).

Деталь — Z-6.3.5.

**Q6 ✅ (принят вариант B с обоснованием):** клиент УЖЕ на проде (Steps 1-6 задеплоены ~2026-08-04), и токены с `scope="sse:today"` живой код выпускает (`apps/backend/app/services/sse/sse_token.py:64`) и валидирует (`sse_token.py:106` — `if payload.get("scope") != SSE_TOKEN_SCOPE: raise InvalidServiceTokenError`). Смена константы `SSE_TOKEN_SCOPE` = breaking change: все живые токены старого клиента станут невалидными → 401 → EventSource закрывается → «SSE иногда работает, иногда нет».

**Контракт SSE-токена после PR #4 (backward-compat):**

```text
SSE_TOKEN_SCOPE_TODAY = "sse:today"      # уже есть, не трогаем
SSE_TOKEN_SCOPE_HABIT_EVENTS = "sse:habit_events"  # NEW, для useHabitSse

# Валидация принимает оба:
if payload.get("scope") not in (SSE_TOKEN_SCOPE_TODAY, SSE_TOKEN_SCOPE_HABIT_EVENTS):
    raise InvalidServiceTokenError()

# Выдача токена:
def issue_token(*, user_id, habit_id, scope="sse:today"):
    """scope="sse:habit_events" выдаёт useHabitSse,
       scope="sse:today" — legacy useTodayStream (поведение по умолчанию)."""
    payload = {..., "scope": scope}
```

**`apps/backend/app/api/v1/events.py:POST /events/stream/token` — новая логика:**

```text
class SseTokenRequest(BaseModel):
    habit_id: str
    scope: str = "sse:today"  # default legacy, новый клиент передаёт "sse:habit_events"

@router.post("/events/stream/token")
async def issue_sse_stream_token(body: SseTokenRequest, ...):
    ...
    if body.scope not in ("sse:today", "sse:habit_events"):
        raise InvalidServiceTokenError()  # 401
    token, exp = generate_sse_token(user_id=user.id, habit_id=body.habit_id,
                                     secret=secret, scope=body.scope)
```

**`apps/backend/app/services/sse/sse_token.py:generate_sse_token`:**

```text
def generate_sse_token(*, user_id, habit_id, secret, scope, ttl_seconds=...):
    payload = {"sub": str(user_id), "habit_id": habit_id, "scope": scope, ...}
    return jwt.encode(payload, secret, algorithm="HS256")
```

(Сигнатура `generate_sse_token` получает параметр `scope`, по умолчанию `"sse:today"` для обратной совместимости с тестами.)

**Frontend `useHabitSse`:** вызывает `sseTokenApi.request(habitId, scope="sse:habit_events")`. Существующий `useTodayStream` оставляет `scope` дефолтным (=`sse:today`).

**Deprecation plan:** `scope="sse:today"` остаётся в коде навсегда (стоимость поддержки = 2 строки в константах + 1 условие в валидаторе). Удалить можно будет, когда все клиенты перейдут на `useHabitSse` и никто не запрашивает legacy-токены (отдельная задача).

**Тесты в `apps/backend/tests/test_sse_token.py`:**
- Старый токен с `scope="sse:today"` → валидируется.
- Новый токен с `scope="sse:habit_events"` → валидируется.
- Неизвестный scope → 401.
- `POST /events/stream/token` с `scope="sse:today"` (default) → выдаёт legacy-токен.
- `POST /events/stream/token` с `scope="sse:habit_events"` → выдаёт новый токен.

**Q7 ✅ (принят вариант B с уточнением):** Redis-guard `dm_sent:{penalty_id}` (TTL 7 дней) — основная защита от дублей DM при worker-retry. `Penalty.notify_sent` (БД-поле) **оставить** — он полезен отдельно, для аудита и отображения в админке «уведомление отправлено?». Redis-guard решает «не отправить дважды», БД-поле решает «показать статус потом». Деталь — Z-10.3.

**Q8 ✅:** статичный текст `_format_violator_dm_text`. Без ротации. AI-комендант с ротацией мотивационных фраз — v2 (`AGENT_BOOTSTRAP.md` §15).

**Регрессионный пробел (Z-2.8) ✅:** API catch-handler не ловит `IntegrityError` (существующий баг, не связан с Z-2). Добавлен `except IntegrityError → CatchResponse(ok=False, code="penalty_already_processed")` в PR #1. UNIQUE constraint `uq_penalty_per_day_reason` защищает на уровне БД, handler превращает IntegrityError в корректный API-response. Тест на параллельные POST `/catch` добавлен. Деталь — Z-2.8.

**Аудит имён методов и контрактов (META, проверка 2026-08-07):** первоначальный план использовал `publish_to_user` как «имя существующего метода» в `EventPublisher` — это была ошибка, метода с таким именем в коде нет. Актуальное имя — `publish_checkin(*, user_id, habit_id, membership_id, date_iso, event: CheckinEvent) -> bool` (`apps/worker/worker/services/event_publisher.py:87-95`). Принято: переименовать в плане, новый метод `publish_to_habit` — добавляется рядом в тот же класс. Деталь — Z-6.2, Z-6.4. Дополнительно проверены:
- `apps/backend/app/services/sse/redis_stream_bus.py:79` — stream key формат `sse:user:{user_id}:{habit_id}` совпадает с планом ✅
- `apps/backend/app/services/sse/sse_token.py:32-34` — audience=`sse-stream`, scope=`sse:today`, issuer=`backend` совпадают ✅
- `apps/backend/app/services/sse/connection_limiter.py:41` — `MAX_CONCURRENT_CONNECTIONS_PER_USER = 5` (per-user лимит). Мультиплексирование в PR #4 = 1 соединение, лимит не превышен ✅

---

## 5. Зависимости от других задач (что блокирует что)

| Блокирующая задача | Блокирует |
|---|---|
| PR #1 (Z-2 миграция) | PR #2, #3, #4, #5, #6 |
| PR #4 (SSE habit-strim) | PR #5 (DM violator использует SSE-событие как сигнал обновить wallet) |
| (нет блокировок) | Z-11 (UI cleanup — независимо) |

**Критический путь:** PR #1 → PR #4 → PR #5. Без PR #1 ничего не работает. Без PR #4 DM violator можно слать только синхронно в worker'е (текущий подход в `_send_catch_notification`), но без real-time wallet-обновления.

---

## 6. Ритуал поддержания доков

После успешной реализации каждого PR — обновить:

| PR | Затронутые доки |
|---|---|
| PR #1 | `Pravki.md` §6.2 (penalty audit) — обновить `apply_catch` на работу с user-deposit, `docs/06-data-model.md` §3 (миграции — добавить 014a, 014b), `docs/09-prod-readiness.md` §1.1 (список выполненных миграций). |
| PR #2 | `Pravki.md` §3.2 (TopUpModal — был radio, теперь без), `docs/05-ui-ux.md` (если описаны денежные флоу). |
| PR #3 | `apps/bot/tests/STATUS.md` (если есть), `Pravki.md` §6.2 (бот проверки), `docs/06-data-model.md` §3 (новый endpoint). |
| PR #4 | `docs/archive/2026-summer-fixes/sse+redis.md` §2.3 (структура стримов — добавить `sse:habit:*`), `Pravki.md` §7 (SSE-фича для чек-инов — теперь мультиплекс + два курсора). |
| PR #5 | `Pravki.md` §6.2 (DM violator — добавить в антифрод-секцию). |
| PR #6 | `Pravki.md` §7.6 (лидерборд — zero_count). |

Каждый PR — отдельный коммит `feat/fix/backend|frontend|bot|worker: ...` с автором `Vegass / dmitriy@vegass.dev`.

Push — только после явного «ок» пользователя.

---

## 7. Что НЕ делаем в этом плане (deferred)

- ❌ **AI-комендант** (мотивационные сообщения с ротацией) — v2 (см. `AGENT_BOOTSTRAP.md` §15).
- ❌ **Ротация catch-нотификаций** (текст «поймал N раз подряд» и т.п.) — v2.
- ❌ **Полный DR testing** — отдельная задача.
- ❌ **Модерация чатов** — v2.
- ❌ **Standalone web app** — вне scope.
- ❌ **Отдельный admin-api** с другим `aud` claim — текущий owner-gate достаточен.
- ❌ **Anti-suspicious_pairs автобан** — эвристика есть, автобан нет (см. `docs/06-data-model.md` §5).
- ❌ **Source of truth для wallet в Redis-кэше** — пока SQL на каждый `/me/wallet`. При масштабе 1000+ RPS пересмотрим (отдельная задача).
- ❌ **Стрим на каждый тип события** (`sse:habit:{id}:leaderboard`, `sse:habit:{id}:catch`) — избыточно, один habit-strim с разными `event` полями достаточен.

---

## 8. Финальный чек-лист перед стартом

- [ ] Прочитан `docs/archive/2026-summer-fixes/sse+redis.md` (1129 строк, особенно §2.3 «Структура Redis-ключей» и §2.4 «Last-Event-ID и реконнект»).
- [ ] Прочитан `AGENTS.md` (правила поведения агента).
- [ ] Прочитан `AGENT_BOOTSTRAP.md` (особенно §3 «ДВА .env файла», §6 «Git и коммиты», §12 «Ритуал поддержания доков»).
- [ ] Прочитан `docs/04-code-standards.md` (layered architecture, DI).
- [ ] Все 4 правки A/B/C/D учтены.
- [ ] Q1-Q8 закрыты, ответы пользователя учтены.
- [ ] Архитектурное дополнение по Last-Event-ID (Z-6.3.1) явно прописано.
- [ ] `pg_dump` ритуал — первым шагом Z-2.
- [ ] Recompute в той же транзакции (Q1), user-lock бесплатно сериализует.
- [ ] Один broadcast через habit-strim (Q2/D).
- [ ] Два курсора Last-Event-ID в backend и frontend.
- [ ] **Legacy `last_event_id` оставлен как fallback для уже-работающего `useTodayStream` в проде** (Q5, исправлено 2026-08-07). Сервер поддерживает три режима: новый (мультиплекс), legacy (single user-strim), fresh (без resume).
- [ ] **`scope="sse:today"` остаётся как legacy**, добавлен опциональный `scope="sse:habit_events"` для `useHabitSse` (Q6, исправлено 2026-08-07). Валидатор принимает оба.
- [ ] Redis-guard `dm_sent:{penalty_id}` (TTL 7 дней) для DM-дедупликации (Q7).
- [ ] `Penalty.notify_sent` остаётся для аудита (Q7).
- [ ] IntegrityError handler в API catch-endpoint (Z-2.8).
- [ ] **Имена методов в плане соответствуют реальному коду** (META): `publish_checkin` (не `publish_to_user`), `publish_to_habit` (новый метод, добавляется в `EventPublisher`). Проверять `grep "def publish" apps/worker/worker/services/event_publisher.py` перед merge PR #4.
- [ ] **Stream key формат `sse:user:{user_id}:{habit_id}`** подтверждён в `apps/backend/app/services/sse/redis_stream_bus.py:79` (порядок user_id первым, потом habit_id, разделитель `:`). Расхождение = тихий баг (XREAD на несуществующий ключ → нет событий, без ошибки).
- [ ] **Idempotency key схема** для `publish_to_habit` использует `sse_published:{event_name}:{habit_id}:{unique_event_id}`, **не** пересекается с `sse_published:checkin:{membership_id}:{date_iso}` (защита от дублей per-event, не per-юзер-день).
- [ ] **`publish_checkin` имеет keyword-only kwarg `event_type: str = "checkin"`** (COLLISION-фикс 2026-08-07). Вызов для `you_were_caught` передаёт `event_type="caught"` явно → namespace `sse_published:caught:{m}:{d}` независим от `sse_published:checkin:{m}:{d}`. Существующие call-сайты (`process_checkin.py:112, 150`) не передают параметр → дефолт `"checkin"` → байт-в-байт совместимо. Регрессионный тест `test_you_were_caught_does_not_collide_with_checkin_rejected_same_day` присутствует в `test_event_publisher.py`.
- [ ] Pre-deployment проверка event-listener'ов в новом bundle (Z-6.6).
- [ ] UNIQUE constraint `uq_penalty_per_day_reason` присутствует (миграция 002) — defense-in-depth.
- [ ] Автор коммитов: `Vegass / dmitriy@vegass.dev` (обязательно).
- [ ] Push — после «ок» пользователя.