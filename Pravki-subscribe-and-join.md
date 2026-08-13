# Pravki-subscribe-and-join.md — план фичи «оплата подписки+депозита при первом вступлении»

> **Snapshot 2026-08-08 (финал, после двух итераций ревью).**
> Версия 1 (после диагностики Q1-Q3): содержала З-13 step 4 в формулировке
> «PAUSED/LEFT → не реактивируем здесь» + «JoinPayModal всегда» (внутреннее
> противоречие: race с двойным списанием подписки у PAUSED-участника с действующим
> `subscription_until`).
>
> Версия 2 (после первого ревью): Z-13 переписан с 3-кейсовой логикой (3a/3b/3c),
> добавлена §Z-13.1 (матрица server-side gate), §Z-13.2 (семантика `subscription_until`/
> `joined_at` при реактивации), переписана §8 Q2.
>
> Версия 3 (финал, эта редакция): добавлена §Z-13.3 — тип `Transaction` отражает что
> реально произошло: кейс 3b → `TransactionType.DEPOSIT_TOPUP` (а не `SUBSCRIPTION`),
> для семантической честности данных в будущей истории транзакций. Добавлены тесты
> в §Z-18.1 (пункт 8 и 9) на новые инварианты.
>
> Документ для реализации. Никакого кода — текстовый план с указанием файлов,
> контрактов, hot-path оценок и тестов. Та же структура, что `Pravki-deposit-sse.md`.
>
> **Зачем:** обнаружено (см. `docs/AGENT_BOOTSTRAP.md` §9 и `Pravki-deposit-sse.md` §Z-2), что
> оплата подписки (`habits.price_month`) как реальный денежный поток **мёртв**:
> бот не вызывает `bot.send_invoice`, webhook-флоу (`bot/handlers/payments.py →
> POST /internal/payments/confirm → worker process_payment → PaymentService.confirm_subscription`)
> существует, но никогда не запускается. На проде 0 транзакций типа `subscription`.
> При этом кнопка «Вступить» на `/marketplace` показывает misleading alert
> «Не удалось зачислить подписку» (источник: `apps/frontend/src/shared/ui/JoinButton.tsx:72`
> в PR #2) при 403 `insufficient_deposit` — потому что deposit-check срабатывает, а
> подписка не списывается вообще, но текст унаследован из старого UX.

---

## 0. Контекст и принципы

- **Стек:** как в `Pravki-deposit-sse.md §0`. Backend = Python 3.12 + FastAPI 0.115 +
  SQLAlchemy 2.0 + asyncpg + Pydantic 2.10 + structlog. Frontend = React 18 + TS 5 +
  Vite 6 + React Query 5 + Zustand 5 + `@telegram-apps/sdk` 3.3. Все правила
  из `AGENTS.md` и `PRIVICHKI.md` (layered architecture, DI, `int` копейки,
  `user_id` только из `request.state.telegram_user`, PII не логировать).
- **Реализованная база (на проде с 2026-08-08):** PR #1 (`ac6951f`) + PR #2 (`9736b5b`)
  + fix LEFT/PAUSED bypass (`ae6bd07`, на feature-ветке `fix/left-paused-deposit-bypass`,
  задеплоен). Deposit живёт на `users.deposit_balance`. `MembershipService.recompute_pause_status`
  синхронизирует статусы клубов. `POST /habits/{id}/join` создаёт ACTIVE membership при
  `deposit_balance >= penalty_amount`, реактивирует LEFT/PAUSED→ACTIVE (без проверки депозита
  для LEFT — отменено фиксом `ae6bd07`, проверка ВСЕГДА).
- **Платежи = мок (см. `AGENT_BOOTSTRAP.md` §9).** `PaymentService.confirm_deposit_topup`
  работает через `POST /api/v1/payments/topup` с `charge_id="mock:{uuid4()}"`. Реальный
  Telegram Payments (через webhook от бота) — не подключён. Новая фича работает на том же
  мок-уровне.
- **`habits.price_month` существует в схеме** (`apps/backend/app/models/habit.py:37`,
  `Integer`, `NOT NULL`), передаётся в `HabitOut` (`apps/backend/app/schemas/__init__.py:54`)
  и отображается на `/marketplace` как «Подписка X ₽/мес». **Нигде не списывается.**
  В новой фиче становится частью фактического платежа.
- **`membership.subscription_until`** (`apps/backend/app/models/membership.py:52`,
  `Date | None`) — metadata-поле, сейчас устанавливается только через
  `PaymentService.confirm_subscription` (мёртвый код). В новой фиче устанавливается
  на `today + 30 дней` при первом вступлении; на повторных вступлениях (LEFT/PAUSED→ACTIVE)
  не трогается.
- **Production snapshot 2026-08-08:** 0 юзеров с реальными деньгами, 3 клуба в `habits`,
  0 memberships, alembic at `014b_drop_membership_dep`. Лоу-риск для новой миграции (не нужна).

### Принципы новой фичи (от пользователя, 2026-08-08)

1. **Первое вступление** — один объединённый платёж: `habits.price_month` (подписка) +
   `deposit_amount` (выбранная сумма депозита). После успешной оплаты сразу создаётся
   ACTIVE membership в той же транзакции.
2. **Повторное открытие** (LEFT/PAUSED→ACTIVE) через существующий `POST /habits/{id}/join` —
   остаётся как есть: только проверка депозита, без подписки. Подразумевается, что
   подписка была оплачена ранее (на первом вступлении).
3. **Persistence — через БД, не через localStorage.** После успешной оплаты `subscription_until`
   установлен, `deposit_balance` пополнен, `membership.status = ACTIVE`. При обновлении
   страницы фронт видит существующую membership через `GET /marketplace` или `GET /me/wallet`,
   никаких отдельных клиентских флагов.
4. **UX (от пользователя, 2026-08-08):**
   - Кнопка первого шага — «Вступить» (как сейчас, без изменений).
   - Открывает модалку `JoinPayModal` со следующим содержимым:
     - Заголовок: «Вступить в клуб «{title}»»
     - Текст: «Подписка {price_month} ₽/мес» (декларативно, не выбирается)
     - **Чекбокс согласия**: «Согласен на подписку {price_month} ₽/мес» — обязателен.
     - Блок выбора суммы депозита — пресеты из `topupPresets.ts` (250/500/750/1000 ₽),
       отфильтрованные: убираются те, что меньше `penalty_amount` этого клуба.
       (Не «выбрал сумму = согласился», а явный чекбокс — UX-требование пользователя.)
     - Кнопка «Оплатить {total} ₽», где `total = price_month + chosen_deposit`.
       **Disabled**, пока чекбокс не отмечен И сумма не выбрана.
   - При успехе — navigate на `/habits/{id}/today` БЕЗ `window.location.reload()`.
5. **Existing `POST /api/v1/payments/topup` остаётся без изменений.** Используется для
   пополнения депозита у уже-вступивших участников (`TopUpModal` на `/today`).
6. **LEFT/PAUSED bypass fix из `ae6bd07` остаётся в силе** и для нового флоу тоже.
   `MembershipService.join` не делает исключений для LEFT/PAUSED — deposit-check всегда.

---

## 1. Сводка изменений (что новое, что переиспользуем)

| Что | Было | Стало |
|---|---|---|
| Endpoint для первого вступления | `POST /habits/{id}/join` (создаёт ACTIVE, проверяет deposit) | `POST /api/v1/payments/subscribe` (новый): списывает подписку + депозит, создаёт ACTIVE |
| `MembershipService.join` | entry point для вступления, проверяет депозит, создаёт membership | только для реактивации LEFT/PAUSED→ACTIVE (при повторных входах на `/today`); проверяет депозит, НЕ создаёт новую membership, НЕ списывает подписку |
| `MembershipService.subscribe_and_join` (новый) | — | единый платёж подписка+депозит + создание ACTIVE membership в одной транзакции; внутри использует `lock_for_update` на user |
| `Transaction.type = "subscription"` | никогда не пишется (мёртвый webhook-флоу) | пишется через `subscribe_and_join` с `charge_id="mock:{uuid4()}"` |
| `Membership.subscription_until` | `None` для всех (никогда не устанавливается) | `today + 30 days` после успешного `subscribe_and_join`; не меняется на реактивации |
| `JoinButton.tsx` | прямой вызов `useJoinHabit.mutate(habit.id)` (PR #2) | открывает `JoinPayModal`, после успеха → `useNavigate(/habits/{id}/today)` |
| `JoinPayModal.tsx` (новый) | — | чекбокс подписки + выбор суммы депозита из `topupPresets`, кнопка «Оплатить X ₽», вызывает `useJoinAndPay.mutate()` |
| `useJoinAndPay()` (новый hook) | — | обёртка над `paymentsApi.subscribe({habit_id, deposit_amount_kopecks, idempotency_key})`, invalidate `["marketplace","today","wallet"]` на success |
| Alert «Не удалось зачислить подписку» | живёт в `JoinButton.tsx:72` fallback для не-403 ошибок | **удаляется**, заменяется на нейтральный «Не удалось вступить. Попробуй ещё раз.» (текст обсудим отдельно) |

### Что НЕ меняется

- ❌ `PaymentService.confirm_subscription` и `confirm_deposit_topup` — остаются как есть
  (используются для отдельных topup'ов существующих участников).
- ❌ `POST /habits/{id}/join` — остаётся как есть (для реактивации).
- ❌ `POST /api/v1/payments/topup` — остаётся как есть (для пополнения депозита).
- ❌ `TopUpModal` — остаётся как есть (используется на `/today` для topup'а).
- ❌ `InsufficientDepositModal` — остаётся как есть (для join без денег — но в новой фиче
  эта ветка фактически не достижима, потому что subscribe-флоу требует выбора депозита
  из отфильтрованных пресетов, ≥ `penalty_amount`).
- ❌ `useJoinHabit` hook — остаётся как есть (используется `MembershipService.join` для
  реактивации, если кто-то зайдёт через эту ветку; в UI не вызывается напрямую после фичи,
  но hook не удаляем — может пригодиться).
- ❌ `MembershipService.recompute_pause_status` — остаётся как есть (вызывается в
  `subscribe_and_join` после мутации `deposit_balance`).

---

## 2. Карта зависимостей (новые ветки поверх существующих)

```
PR #1 (ac6951f)              ← уже на проде
  ↓
PR #2 (9736b5b)              ← уже на проде
  ↓
fix LEFT/PAUSED (ae6bd07)    ← на проде (feature-ветка)
  ↓
PR #7: Z-12..Z-18 (этот план) — «subscribe and join» объединённый платёж
```

Зависимостей от PR #3..#6 из `Pravki-deposit-sse.md §2` нет — этот план самодостаточен.

---

## Z-12. Архитектура: `POST /api/v1/payments/subscribe` как единая точка входа

**Severity:** P0 (новая фича, основной путь вступления в клуб).

### Z-12.1 Контракт endpoint

**Файл:** `apps/backend/app/api/v1/payments.py` (расширение, не новый файл).

```text
POST /api/v1/payments/subscribe
Auth: X-Telegram-Init-Data (через TelegramUserDbDep, как /api/v1/payments/topup)

Request body (SubscribeRequest, Pydantic):
  habit_id: str                          # required, UUID клуба
  deposit_amount_kopecks: int            # required, gt=0, le=10_000_000
  subscription_accepted: bool            # required. Семантика:
                                        #   - если у юзера нет активной подписки →
                                        #     обязательно True (иначе 422 subscription_required)
                                        #   - если у юзера есть активная подписка
                                        #     (existing.subscription_until >= today) →
                                        #     допустимо и True, и False: backend всё равно
                                        #     не списывает price_month (см. §Z-13.1 матрица)
  idempotency_key: str                   # required, client-generated UUID4 (для safe-retry)

Response 200 OK (SubscribeResponse):
  ok: true
  transaction_id: str                    # UUID транзакции (тип — см. §Z-13 шаг 9)
  membership_id: str                     # UUID новой/реактивированной membership
  new_deposit_balance: int               # копейки после зачисления
  subscription_until: str                # ISO date "YYYY-MM-DD" (today + 30 для новой,
                                        # или существующая дата если подписка не трогалась)
  total_charged_kopecks: int             # фактически списано (= price_month+deposit или =deposit)
  charged_subscription: bool             # NEW: True если списали price_month,
                                        #      False если только deposit
                                        # (см. §Z-13.1 — внутренний контракт)

Errors:
  404 habit_not_found              — клуба не существует или archived
  409 already_active                — у юзера уже ACTIVE membership (refresh страницы)
  422 insufficient_deposit_choice   — deposit_amount < penalty_amount (UI баг)
  422 subscription_required         — нет активной подписки И subscription_accepted=False
  400 idempotency_conflict          — тот же idempotency_key с другими параметрами
```

### Z-12.2 Почему новый endpoint, а не расширение `POST /habits/{id}/join`

- **Разные контуры авторизации / rate-limit'а:** `/api/v1/payments/*` — финансовый контур
  (60/60s, см. `core/constants.py:HttpRateLimitConfig.RATE_LIMIT_API_V1`). `/api/v1/habits/*` —
  membership-контур. Смешивать неправильно.
- **Разные идемпотентности:** `subscribe` пишет `Transaction` с `idempotency_key`,
  `join` — не пишет транзакцию вообще. Склеивать через общий сервис не нужно.
- **Разные фронт-флоу:** `subscribe` → модалка с оплатой; `join` → без оплаты
  (предполагается, что депозит уже на балансе).
- **Backward-compat:** существующие клиенты `useJoinHabit` (если есть, например бот
  или admin-скрипты) продолжают работать без изменений.

### Z-12.3 Что НЕ делает `POST /api/v1/payments/subscribe`

- ❌ Не обновляет `habit.prize_pool`. Подписка идёт в депозит (см. §0 — «всё на
  `user.deposit_balance`»), а не в призовой фонд. Призовой фонд пополняется через
  `PenaltyService.apply_catch` (мёртвый flow для подписки).
- ❌ Не продлевает существующий `subscription_until`. Если у юзера уже был
  `subscription_until`, он остаётся как есть. Семантика — «купил месяц подписки
  при вступлении», не «продлил подписку». Продление — отдельная фича (v2,
  см. §5 «Что НЕ делаем»).
- ❌ Не реагирует на `subscription_accepted=False`. Server-side gate: запрос с
  `subscription_accepted=False` → 422 `subscription_required`. Это защита от
  попыток обойти чекбокс через прямой POST.

---

## Z-13. Backend: `MembershipService.subscribe_and_join`

**Файл:** `apps/backend/app/services/membership_service.py` (новый метод).

```text
async def subscribe_and_join(
    self,
    *,
    user_id: int,
    habit_id: str,
    deposit_amount_kopecks: int,
    subscription_accepted: bool,
    idempotency_key: str,
) -> tuple[Membership, Transaction, bool]:
    """Единый платёж подписка+депозит + создание/реактивация ACTIVE membership.

    Возвращает (membership, transaction, charged_subscription) — последний флаг показывает,
    списали ли price_month (True) или только deposit (False).

    Поток (атомарно в одной транзакции):
    1. Идемпотентность: SELECT Transaction WHERE idempotency_key == :key.
       Если найдена и параметры совпадают — вернуть (existing_m, existing_tx, existing_charged).
       Если найдена и параметры НЕ совпадают — 400 idempotency_conflict.
    2. habit = habit_repo.get(habit_id). None → 404 HabitNotFoundError.
       habit.archived_at is not None → 404 HabitNotFoundError.
       habit.is_active is False → 409 HabitInactiveError.
    3. existing = membership_repo.get_for_user_in_habit(user_id, habit_id).
       Разбираем 3 кейса (см. §Z-13.1 матрица и §Z-13.2 семантика):
       3a. existing is None ИЛИ (existing.status in (PAUSED, LEFT) И
           (existing.subscription_until is None ИЛИ existing.subscription_until < today)):
           → charged_subscription = True. Нужна полная оплата.
           Если subscription_accepted == False → 422 subscription_required.
       3b. existing is not None И existing.status in (PAUSED, LEFT) И
           existing.subscription_until >= today:
           → charged_subscription = False. Подписка уже оплачена.
           subscription_accepted допустимо и True, и False (UI просто не показывает чекбокс).
       3c. existing.status == ACTIVE → 409 already_active (refresh страницы).
    4. SELECT FOR UPDATE на user (user_repo.lock_for_update).
       None → UserNotFoundError.
    5. Валидация deposit_amount: должен быть >= habit.penalty_amount.
       Иначе 422 insufficient_deposit_choice (защита от UI-багов).
    6. Применяем эффект на membership:
       - Кейс 3a (existing is None): создаём новую Membership
         (status=ACTIVE, joined_at=now, subscription_until=today+30d).
       - Кейс 3a (existing in PAUSED/LEFT): existing.status = ACTIVE,
         existing.subscription_until = today+30d.
         joined_at НЕ трогаем (см. §Z-13.2).
       - Кейс 3b (PAUSED/LEFT с активной подпиской):
         existing.status = ACTIVE. subscription_until и joined_at НЕ трогаем.
       - Кейс 3c: raise AlreadyActiveError (пойман выше).
    7. u.deposit_balance += (итого списания):
       - Кейс 3a: += (habit.price_month + deposit_amount_kopecks).
       - Кейс 3b: += deposit_amount_kopecks (только депозит).
    8. Создаём Transaction:
       - Кейс 3a: type = TransactionType.SUBSCRIPTION,
         amount = (habit.price_month + deposit_amount_kopecks),
         balance_after = u.deposit_balance,
         related_membership_id = m.id,
         idempotency_key = idempotency_key.
       - Кейс 3b: type = TransactionType.DEPOSIT_TOPUP,    # ← см. §Z-13.3 — тип
         amount = deposit_amount_kopecks,                   #   транзакции отражает
         balance_after = u.deposit_balance,                 #   что реально произошло,
         related_membership_id = m.id,                      #   а не "всё подписка"
         idempotency_key = idempotency_key.
    9. recompute_pause_status(user_id) — для всех клубов юзера (включая текущий).
       MembershipService.recompute_pause_status уже умеет это.
    10. session.flush() — отлавливаем IntegrityError на idempotency_key UNIQUE.
        При гонке с параллельным запросом — возвращаем существующую транзакцию.
    11. Возвращаем (membership, transaction, charged_subscription).

    Не коммитит (commit на уровне handler'а, см. §Z-14.3).
    """
```

### Z-13.1 Server-side gate: матрица `subscription_active × subscription_accepted`

`existing.subscription_until` сравнивается с `date.today()` на момент запроса. Если в будущем
(или сегодня) — подписка считается активной.

| `existing.subscription_until >= today` | `subscription_accepted` | Результат |
|---|---|---|
| True (подписка активна) | True | ✅ 200 OK, `charged_subscription: false`, списываем только deposit |
| True (подписка активна) | False | ✅ 200 OK, `charged_subscription: false`, списываем только deposit (UI просто не показал чекбокс) |
| False (нет подписки) | True | ✅ 200 OK, `charged_subscription: true`, списываем sub+deposit |
| False (нет подписки) | False | ❌ 422 `subscription_required` |

**Почему True+Active и False+Active оба OK:** UI адаптируется на основе `myHabits`
(см. §Z-16.2 ниже). Если у юзера есть активная подписка — чекбокс в модалке вообще не
рисуется, поле `subscription_accepted` шлётся как `false` (дефолтное значение формы).
Если кто-то шлёт `true` в обход — это безвредно, потому что сервис всё равно знает,
что подписка активна и `charged_subscription=false` будет выставлен независимо.

**Защита от обхода:** единственная реальная защита нужна только для случая «нет подписки
+ не согласился» — это 422. Все остальные комбинации корректны.

### Z-13.2 Семантика `subscription_until` / `joined_at` при реактивации

При реактивации LEFT/PAUSED (любой из 3a/3b):

- **`joined_at` НЕ трогается ни в одном кейсе.** Это дата первого вступления, не реактивации.
  Если потом понадобится аудит «как давно юзер с нами» — смотрим на `joined_at`.

- **Кейс 3a (полная оплата подписки заново):**
  `subscription_until = today + 30 days`. Это новая оплата месяца — обновляем срок.

- **Кейс 3b (подписка уже была оплачена, реактивируем после исчерпания депозита):**
  `subscription_until` остаётся как был. Это семантически правильно: подписка
  действующая, депозит пополнили, членство восстановлено. Никакого нового списания
  подписки не происходит (защита от двойного биллинга — обоснование см. §Q2 в ревью).

При новом вступлении (3a, existing=None):
- `subscription_until = today + 30 days`.
- `joined_at = now`.

### Z-13.3 Тип Transaction отражает что реально произошло

Это **не** вопрос «для аналитики когда-нибудь потом», это вопрос семантической честности
данных с первого дня. См. ревью пользователя 2026-08-08:

- Кейс 3a → `TransactionType.SUBSCRIPTION` (списали подписку — это подписочная транзакция).
- Кейс 3b → `TransactionType.DEPOSIT_TOPUP` (подписку НЕ списывали, это пополнение депозита,
  как через `POST /payments/topup`).

Если оставить `type=SUBSCRIPTION` для всех случаев, то в будущей истории транзакций
(`GET /me/transactions`, см. §6 deferred) разработчик увидит «Подписка +300₽» там, где
реально было пополнение депозита — это либо пустая трата времени на расследование, либо
(хуже) показ неверной истории пользователю.

Используется существующее enum-значение `TransactionType.DEPOSIT_TOPUP` (`apps/backend/app/core/constants.py:35`,
применяется в `PaymentService._apply` для `confirm_deposit_topup`, `payment_service.py:136`).
Никакого нового enum-значения не нужно — семантика точно совпадает с «пополнили депозит».

### Z-13.4 Контракт `SubscribeRequest` / `SubscribeResponse`

**Файл:** `apps/backend/app/schemas/__init__.py`

```python
class SubscribeRequest(BaseModel):
    habit_id: str
    deposit_amount_kopecks: int = Field(gt=0, le=10_000_000)
    subscription_accepted: bool
    idempotency_key: str = Field(min_length=8, max_length=128)


class SubscribeResponse(BaseModel):
    ok: bool = True
    transaction_id: str
    membership_id: str
    new_deposit_balance: int
    subscription_until: date
    total_charged_kopecks: int
    charged_subscription: bool   # NEW: True = списали price_month+deposit,
                                 #      False = списали только deposit
```

### Z-13.5 Новые исключения (если нужны)

**Файл:** `apps/backend/app/core/exceptions.py`

- `HabitInactiveError` — если habit.is_active == False. (Сейчас используется в
  `apps/backend/app/api/v1/memberships.py:_ensure_joinable` — переиспользуем.)
- `SubscriptionRequiredError` — 422, `code="subscription_required"`. Только для кейса 3a:
  нет активной подписки И `subscription_accepted=False`.
- `InsufficientDepositChoiceError` — 422, `code="insufficient_deposit_choice"`.
  `required_kopecks=penalty_amount`, `chosen_kopecks=deposit_amount`.
  Отдельный от `InsufficientDepositError` (который 403, для `/join` без денег).
- `AlreadyActiveError` — 409, `code="already_active"`. UI: «Ты уже в клубе, обнови страницу».
- `IdempotencyConflictError` — 400, `code="idempotency_conflict"`. UI: «Ошибка оплаты,
  попробуй ещё раз» (на практике не должно случаться если клиент генерит uuid4 правильно).

### Z-13.6 Lock-ordering и транзакция

```text
BEGIN
  SELECT Transaction WHERE idempotency_key = :key   # идемпотентность (шаг 1)
  SELECT habits WHERE id=:habit_id                  # без лока, только чтение
  SELECT memberships WHERE user_id=:u AND habit_id=:h  # без лока
  SELECT FOR UPDATE users WHERE id=:user_id         # lock_for_update (шаг 4)
  INSERT/UPDATE memberships (...)                   # шаг 6
  UPDATE users SET deposit_balance += :total        # шаг 7
  INSERT transactions (...)                         # шаг 8, idempotency_key UNIQUE
  SELECT m, habit.penalty_amount FROM memberships JOIN habits ...  # для recompute
  UPDATE memberships SET status = ...               # внутри recompute (шаг 9)
COMMIT
```

**Lock-order:** user-lock берётся ПЕРЕД insert/update. Это означает, что параллельный
`/join` или параллельный `/subscribe` для этого же юзера (например, две вкладки
`/marketplace`) повисит на `SELECT FOR UPDATE` пока первая транзакция не закоммитится.
После commit второй запрос увидит уже ACTIVE membership → вернёт 409 `already_active`.
Идемпотентно.

**Почему не `habit.lock_for_update`:** на MVP клуб не редактируется одновременно с
join'ом участника (admin-операции редки). Если в будущем понадобится — добавим
отдельный лок на habit при `member_limit`, как уже сделано в существующем `join`.

### Z-13.7 Hot-path оценка

Один `SELECT FOR UPDATE` + 3-4 простых SELECT + 1-2 INSERT (или UPDATE для 3b) +
1-2 UPDATE = ~7-9 SQL-запросов.
Это в ~2-3 раза больше, чем текущий `join` (там 3-4 запроса). Приемлемо для MVP —
фича вызывается один раз за время жизни membership, не в hot-path.

---

## Z-14. Backend: endpoint `POST /api/v1/payments/subscribe`

**Файл:** `apps/backend/app/api/v1/payments.py` (расширение).

```text
@router.post("/payments/subscribe", response_model=SubscribeResponse)
async def subscribe(
    payload: SubscribeRequest,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> SubscribeResponse:
    """Единая оплата подписки+депозита с созданием ACTIVE membership.

    MVP-мок. Идемпотентность через client-supplied idempotency_key
    (uuid4 из фронта, можно ретраить безопасно).

    Безопасность:
    - TelegramUserDbDep: initData проверен middleware; user.id — авторитетный.
    - subscription_accepted: server-side gate (нельзя обойти чекбокс через прямой POST).
    - deposit_amount_kopecks: gt=0, le=10M (cap 100k ₽).
    - idempotency_key: min_length=8, max_length=128.
    - SELECT FOR UPDATE на user: сериализует параллельные subscribe/topup этого юзера.
    """
    habit_repo = HabitRepository(session)
    user_repo = UserRepository(session)
    membership_repo = MembershipRepository(session)

    service = MembershipService(
        session=session,
        membership_repo=membership_repo,
        habit_repo=habit_repo,
        user_repo=user_repo,
    )

    try:
        m, tx = await service.subscribe_and_join(
            user_id=user.id,
            habit_id=payload.habit_id,
            deposit_amount_kopecks=payload.deposit_amount_kopecks,
            subscription_accepted=payload.subscription_accepted,
            idempotency_key=f"subscribe:{payload.idempotency_key}",
        )
        await session.commit()
    except DomainError:
        await session.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 — payment_failed — единый ответ UI
        await session.rollback()
        log.exception("subscribe_unexpected", extra={"user_id": user.id, "habit_id": payload.habit_id})
        raise HTTPException(status_code=500, detail={"code": "internal_error"})

    return SubscribeResponse(
        ok=True,
        transaction_id=str(tx.id),
        membership_id=str(m.id),
        new_deposit_balance=tx.balance_after or 0,
        subscription_until=m.subscription_until,
        total_charged_kopecks=tx.amount,
    )
```

### Z-14.1 Защита от race при parallel POST с одним `idempotency_key`

`PaymentService._apply` уже имеет паттерн: SELECT existing by idempotency_key →
если найдена, вернуть её. Переиспользуем в `subscribe_and_join`:

```text
existing_tx = await session.execute(
    select(Transaction).where(Transaction.idempotency_key == idempotency_key)
)
if existing_tx.scalar_one_or_none() is not None:
    # Идемпотентный retry. Проверяем что related_membership_id — наш.
    # Если related_membership_id != ожидаемому (т.е. тот же ключ использован
    # с другим habit_id) → IdempotencyConflictError.
    m = await membership_repo.get(existing_tx.scalar_one().related_membership_id)
    if m is None or m.habit_id != habit_id:
        raise IdempotencyConflictError()
    return m, existing_tx.scalar_one()
```

### Z-14.2 Lock-order для `MembershipService.join` (existing)

Текущий `MembershipService.join` берёт user-lock через `user_repo.get(user_id)` —
это **обычный SELECT**, не `FOR UPDATE`. Это работает потому что:
- На проде `/join` вызывается только после успешного пополнения через `/payments/topup`
  (PR #2 фикс LEFT/PAUSED bypass), который сам берёт user-lock.
- В race window (parallel `/join` + `/subscribe`) — оба берут user-lock, второй ждёт.

**Решение для нового `subscribe_and_join`:** явно берём `user_repo.lock_for_update(user_id)`
ДО insert/update (см. §Z-13.4). Это безопасно — внутри одной транзакции lock держится до
commit, второй параллельный запрос ждёт.

### Z-14.3 Commit — на уровне handler'а

Сервис не коммитит (`MembershipService.subscribe_and_join` — это чистая бизнес-логика).
Commit в `apps/backend/app/api/v1/payments.py:subscribe` после успешного возврата
из сервиса. Это правило layered architecture из `AGENTS.md` / `docs/04-code-standards.md`.

---

## Z-15. Backend: `MembershipService.join` — сужение до «реактивации»

**Файл:** `apps/backend/app/services/membership_service.py` (точечная правка).

Текущий `join` (после PR #1+PR #2+ae6bd07) делает две вещи:
1. Создаёт новую ACTIVE membership для нового участника (с проверкой депозита).
2. Реактивирует LEFT/PAUSED → ACTIVE (с проверкой депозита — фикс `ae6bd07`).

После фичи Z-13 новая ACTIVE membership создаётся только через `subscribe_and_join`.
`join` остаётся только для реактивации.

**Что НЕ меняется в `join`:**
- ❌ Не добавляем новый код. Существующая логика реактивации уже корректна (см. §Z-13.5
  `MembershipService.join:37-122` в текущей реализации).
- ❌ Не убираем ветку «создание новой membership» — она остаётся на случай если
  какой-то клиент всё ещё вызывает `/join` напрямую (например, бот в
  `chat_member` handler). В этом случае поведение такое же, как сейчас:
  если membership существует — реактивация, если нет — создание с проверкой депозита.

**Что добавляем:**
- Логирование на INFO-уровне «join_called_for_new_membership» (для диагностики —
  видеть в логах если кто-то всё-таки вызывает `/join` напрямую вместо `/subscribe`).
  Это soft-deprecation signal, не блокер.

```text
# В MembershipService.join после создания новой membership:
if existing is None:
    self._logger.info(
        "join_called_for_new_membership",
        extra={
            "user_id": user_id,
            "habit_id": habit_id,
            "note": "Use POST /api/v1/payments/subscribe for first-time join with payment",
        },
    )
```

**Hot-path:** без изменений, тот же код что сейчас.

### Z-15.1 Почему НЕ удаляем «создание новой membership» из `join`

- Backward-compat: `/join` остаётся публичным endpoint'ом. Кто-то может его вызывать
  (тесты, admin-скрипты, бот в edge-case).
- Безопасность: если у юзера уже есть deposit (например, пополнил через `/topup`
  до того как появилась фича Z-13), `/join` создаст membership. Это работает.
- Минимизация изменений: убирать ветку = риск regression'а в тестах
  (`test_join_with_deposit.py`). Soft-deprecation через логирование достаточно.

---

## Z-16. Frontend: `JoinPayModal` — модалка оплаты при первом вступлении

**Файл (новый):** `apps/frontend/src/shared/ui/JoinPayModal.tsx`

### Z-16.1 Props

```typescript
interface JoinPayModalProps {
  open: boolean;
  onClose: () => void;
  habit: {
    id: string;
    title: string;
    penalty_amount: number;       // копейки
    price_month: number;          // копейки
  };
}
```

### Z-16.2 Состояние

```typescript
const [subscriptionAccepted, setSubscriptionAccepted] = useState(false);
const [selectedPreset, setSelectedPreset] = useState<number | null>(null);
const [customAmount, setCustomAmount] = useState<string>("");

const availablePresets = useMemo(() =>
  DEFAULT_TOPUP_PRESETS_KOPECKS.filter(p => p >= habit.penalty_amount),
  [habit.penalty_amount]
);

const recommendedPreset = useMemo(() =>
  pickPresetToCover(habit.penalty_amount, availablePresets),
  [habit.penalty_amount, availablePresets]
);

const chosenDepositKopecks = selectedPreset ?? (customAmount
  ? parseInt(customAmount, 10) * 100
  : 0);

const totalKopecks = habit.price_month + chosenDepositKopecks;

const canPay = subscriptionAccepted && chosenDepositKopecks > 0;
```

### Z-16.3 UI

```text
┌───────────────────────────────────────┐
│ Вступить в клуб «{habit.title}»       │
│                                       │
│ ┌─────────────────────────────────┐   │
│ │ Подписка                         │   │
│ │ {formatRub(price_month)}/мес    │   │
│ │                                  │   │
│ │ [✓] Согласен на подписку         │   │
│ │     {formatRub(price_month)}/мес│   │
│ └─────────────────────────────────┘   │
│                                       │
│ Депозит                               │
│ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│ │ 250₽ │ │ 500₽★│ │ 750₽ │ │1000₽│  │ ← recommended подсвечен
│ └──────┘ └──────┘ └──────┘ └──────┘  │
│ или своя сумма: [____] ₽              │
│                                       │
│ ─────────────────────────────────     │
│ Итого к оплате: {formatRub(total)}    │
│                                       │
│ ┌─────────────────────────────────┐   │
│ │     Оплатить {formatRub(total)} │   │ ← disabled пока !canPay
│ └─────────────────────────────────┘   │
│                                       │
│ 💳 Скоро: СБП, карты, Telegram Stars  │
└───────────────────────────────────────┘
```

### Z-16.4 Логика кнопки «Оплатить»

```typescript
const handlePay = () => {
  if (!canPay) return;
  hapticImpact("medium");
  subscribeMutation.mutate({
    habit_id: habit.id,
    deposit_amount_kopecks: chosenDepositKopecks,
    subscription_accepted: true,
    idempotency_key: uuidv4(),  // safe-retry
  }, {
    onSuccess: (data) => {
      hapticNotify("success");
      onClose();
      // навигация — на стороне JoinButton (он дёрнет navigate через callback)
      // или прямо здесь — обсудим в Z-17
    },
    onError: (err) => {
      // Маппинг ошибок:
      //   404 habit_not_found        → "Клуб не найден"
      //   409 already_active          → "Ты уже в клубе, обнови страницу"
      //   422 insufficient_deposit_choice → "Выбери сумму ≥ {penalty}"
      //   422 subscription_required   → "Нужно согласие на подписку"
      //   400 idempotency_conflict    → "Ошибка оплаты, попробуй ещё раз"
      //   прочее → generic "Не удалось вступить. Попробуй ещё раз."
      hapticNotify("error");
      void showAlert(formatError(err));
    },
  });
};
```

### Z-16.5 Тесты

**Файл:** `apps/frontend/src/shared/ui/__tests__/JoinPayModal.test.tsx`

- `renders with subscription price and presets filtered by penalty_amount`.
  С `penalty_amount=200_00` (200₽), presets становятся [250, 500, 750, 1000]
  (ничего не отфильтровано, 250₽ >= 200₽).
  С `penalty_amount=600_00` (600₽), presets становятся [750, 1000].
  С `penalty_amount=1500_00` (1500₽), все пресеты отфильтрованы → показывается
  только «своя сумма» input.
- `pay button disabled until subscription checkbox checked AND deposit chosen`.
- `pay button shows correct total = price_month + chosen_deposit`.
- `pay mutation sends correct payload {habit_id, deposit_amount_kopecks, subscription_accepted: true, idempotency_key}`.
- `pay onSuccess closes modal and calls onSuccess callback`.
- `pay onError maps error codes to user-facing Russian strings`.

---

## Z-17. Frontend: `JoinButton` → открывает `JoinPayModal` вместо прямого `/join`

**Файл:** `apps/frontend/src/shared/ui/JoinButton.tsx` (точечный refactor).

### Z-17.1 Текущее поведение (PR #2)

```text
Click → useJoinHabit.mutate(habit.id) → onSuccess navigate
                              → onError 403 insufficient_deposit → InsufficientDepositModal
                                       → прочее → alert "Не удалось зачислить подписку"
```

### Z-17.2 Новое поведение

```text
Click → setJoinPayOpen(true)   # открывает JoinPayModal
                                  │
JoinPayModal.handlePay()
  → useJoinAndPay.mutate(payload)
       → onSuccess → close modal → navigate(/habits/{id}/today)
       → onError  → showAlert(formatError(err))
```

### Z-17.3 Удаление misleading alert

Удалить строку 72 (`alert("Не удалось зачислить подписку. Попробуй ещё раз.")`) и
`InsufficientDepositModal`/`TopUpModal` из `JoinButton` (они больше не нужны — в
новой фиче deposit выбирается внутри `JoinPayModal`).

### Z-17.4 Удалить `useJoinHabit` из `JoinButton`?

`useJoinHabit` остаётся как есть (используется ботом и, потенциально, другими
endpoint'ами). `JoinButton` перестаёт его вызывать, но **сам hook не удаляем** —
он может пригодиться для edge-case'ов.

### Z-17.5 `useJoinAndPay()` hook (новый)

**Файл:** `apps/frontend/src/shared/hooks/useJoinAndPay.ts` (новый).

```typescript
export function useJoinAndPay(onSuccess?: () => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      habit_id: string;
      deposit_amount_kopecks: number;
      subscription_accepted: boolean;
      idempotency_key: string;
    }) => paymentsApi.subscribe(payload).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["marketplace"] });
      qc.invalidateQueries({ queryKey: ["today"] });
      qc.invalidateQueries({ queryKey: ["wallet"] });
      qc.invalidateQueries({ queryKey: ["balance"] });
      onSuccess?.();
    },
  });
}
```

### Z-17.6 `paymentsApi.subscribe()` API wrapper

**Файл:** `apps/frontend/src/shared/api/index.ts`

```typescript
export const paymentsApi = {
  topup: (payload: { habit_id: string; amount_kopecks: number }) =>
    apiClient.post<TopupResponse>("/payments/topup", payload).then(r => r.data),
  subscribe: (payload: {
    habit_id: string;
    deposit_amount_kopecks: number;
    subscription_accepted: boolean;
    idempotency_key: string;
  }) =>
    apiClient
      .post<SubscribeResponse>("/payments/subscribe", payload)
      .then(r => r.data),
};
```

**Тип `SubscribeResponse`** — в `apps/frontend/src/shared/types/index.ts`:

```typescript
export interface SubscribeResponse {
  ok: boolean;
  transaction_id: string;
  membership_id: string;
  new_deposit_balance: number;
  subscription_until: string;  // ISO date "YYYY-MM-DD"
  total_charged_kopecks: number;
}
```

### Z-17.7 Тесты

**Файл:** `apps/frontend/src/shared/ui/__tests__/JoinButton.test.tsx` (обновить).

- `click opens JoinPayModal` (вместо прямого мутирования).
- `JoinPayModal успех → navigate(/habits/{id}/today)` без `window.location.reload()`.
- `JoinPayModal ошибка → showAlert с правильным русским текстом`.
- Старые тесты на `403 insufficient_deposit` → `InsufficientDepositModal` — **удалить**
  (ветка больше недостижима).

---

## Z-18. Тесты (полный список)

### Z-18.1 Backend тесты

**Файл (новый):** `apps/backend/tests/test_subscribe_and_join.py`

Покрытие:

1. **Happy path:**
   - `test_subscribe_creates_active_membership_and_charges_combined_payment`:
     user.deposit_balance=0, habit.price_month=100₽, penalty=200₽,
     deposit_amount=200₽ → итоговый deposit=300₽, membership.status=ACTIVE,
     subscription_until=today+30, transaction(type=SUBSCRIPTION, amount=300₽,
     charged_subscription=True).
   - `test_subscribe_creates_active_membership_for_brand_new_user`:
     existing is None → новая membership создаётся, joined_at=now,
     transaction(type=SUBSCRIPTION, related_membership_id=new_id).
2. **Idempotency:**
   - `test_subscribe_idempotent_with_same_key`:
     повторный POST с тем же idempotency_key + теми же параметрами → возвращается
     та же (membership, transaction, charged_subscription), без нового списания.
   - `test_subscribe_idempotency_conflict_with_different_habit`:
     повторный POST с тем же idempotency_key но другим habit_id → 400 idempotency_conflict.
3. **Server-side gate (матрица §Z-13.1):**
   - `test_subscribe_rejects_subscription_accepted_false_without_active_subscription`:
     existing is None (или subscription_until в прошлом), subscription_accepted=False
     → 422 subscription_required.
   - `test_subscribe_accepts_subscription_accepted_false_with_active_subscription`:
     existing.status=PAUSED, subscription_until=today+10 → 200 OK,
     charged_subscription=False, transaction(type=DEPOSIT_TOPUP, amount=deposit).
   - `test_subscribe_accepts_subscription_accepted_true_with_active_subscription`:
     existing.status=PAUSED, subscription_until=today+10, subscription_accepted=True
     → 200 OK, charged_subscription=False (UI просто прислал true в обход, безвредно).
   - `test_subscribe_charges_full_when_subscription_expired`:
     existing.status=PAUSED, subscription_until=today-1 (вчера) →
     charged_subscription=True, transaction(type=SUBSCRIPTION, amount=sub+deposit).
   - `test_subscribe_charges_full_when_subscription_was_never_paid`:
     existing.status=LEFT (subscription_until=None) → charged_subscription=True,
     transaction(type=SUBSCRIPTION).
4. **Validation:**
   - `test_subscribe_rejects_deposit_below_penalty`:
     deposit_amount < habit.penalty_amount → 422 insufficient_deposit_choice.
   - `test_subscribe_rejects_deposit_zero`:
     deposit_amount=0 → 422 (Field gt=0).
   - `test_subscribe_rejects_unknown_habit`:
     habit_id не существует → 404 habit_not_found.
   - `test_subscribe_rejects_archived_habit`:
     habit.archived_at is not None → 404 habit_not_found.
   - `test_subscribe_rejects_inactive_habit`:
     habit.is_active=False → 409 HabitInactiveError.
5. **Already-active:**
   - `test_subscribe_rejects_already_active_membership`:
     existing membership.status=ACTIVE → 409 already_active.
6. **Lock-ordering:**
   - `test_subscribe_serializes_parallel_calls_for_same_user`:
     два параллельных `subscribe_and_join` для одного user → один успех, второй
     видит уже-созданную membership (после commit первого) → 409 already_active.
     Не должно быть «двух membership'ов» в БД.
7. **Recompute:**
   - `test_subscribe_triggers_recompute_pause_status`:
     user был PAUSED в клубе X (deposit < penalty_X), subscribe в клуб Y с большим
     deposit → после subscribe user.deposit_balance растёт → recompute →
     membership в X становится ACTIVE.
8. **Transaction type семантика (§Z-13.3):**
   - `test_subscribe_active_subscription_creates_deposit_topup_transaction`:
     existing с активной подпиской → в БД ровно одна новая Transaction
     с type="deposit_topup" (НЕ "subscription"). Защита от регрессии
     «вернули обратно type=SUBSCRIPTION для всех случаев».
   - `test_subscribe_full_payment_creates_subscription_transaction`:
     existing=None → Transaction с type="subscription", amount=sub+deposit.
9. **Семантика `subscription_until` / `joined_at` (§Z-13.2):**
   - `test_subscribe_reactivate_with_active_sub_does_not_change_subscription_until`:
     existing.subscription_until=today+10, после subscribe значение НЕ изменилось
     (осталось today+10). joined_at тоже НЕ изменилось.
   - `test_subscribe_reactivate_with_expired_sub_extends_subscription_until`:
     existing.subscription_until=today-1 → после subscribe равно today+30.
     joined_at НЕ изменилось.

### Z-18.2 Существующие тесты — НЕ должны ломаться

- `test_join_with_deposit.py` (Z-3): проверяет существующий `/join` —
  остаётся зелёным, фича его не трогает.
- `test_wallet.py` (Z-4): `/me/wallet` — без изменений.
- `test_user_deposit_balance.py` (Z-2): deposit balance — без изменений.
- `test_recompute_pause_status.py` (Z-2.6): recompute — без изменений.
- `test_payment_service.py` (Z-2.5): `confirm_deposit_topup` — без изменений.

### Z-18.3 Frontend тесты

**Файл (обновить):** `apps/frontend/src/shared/ui/__tests__/JoinButton.test.tsx`
- `JoinButton click opens JoinPayModal`.
- Удалить тесты на 403 `insufficient_deposit` → `InsufficientDepositModal`.

**Файл (новый):** `apps/frontend/src/shared/ui/__tests__/JoinPayModal.test.tsx`
- `renders title, price, penalty, presets filtered`.
- `pay button disabled without subscription_accepted`.
- `pay button disabled without deposit chosen`.
- `pay button shows correct total`.
- `pay mutation sends correct payload with idempotency_key`.
- `pay onSuccess calls onSuccess callback`.
- `pay onError maps 404 to Russian error`.
- `pay onError maps 409 already_active to Russian error`.
- `pay onError maps 422 insufficient_deposit_choice to Russian error`.

**Файл (новый):** `apps/frontend/src/shared/hooks/__tests__/useJoinAndPay.test.ts`
- `mutation calls paymentsApi.subscribe with correct args`.
- `onSuccess invalidates ['marketplace','today','wallet','balance']`.

### Z-18.4 Migration test (если добавляется новая миграция)

Не нужна — данные не мигрируются. Схема таблиц не меняется. Миграция Alembic не требуется.

---

## 3. Сводный план реализации

| Шаг | Задачи | Файлы | Время |
|---|---|---|---|
| **PR #7 (этот план)** | Z-12..Z-18 (backend subscribe_and_join + endpoint + frontend JoinPayModal + JoinButton refactor + tests) | `app/services/membership_service.py`, `app/api/v1/payments.py`, `app/schemas/__init__.py`, `app/core/exceptions.py`, `apps/frontend/src/shared/api/index.ts`, `apps/frontend/src/shared/types/index.ts`, `apps/frontend/src/shared/hooks/useJoinAndPay.ts`, `apps/frontend/src/shared/ui/JoinPayModal.tsx`, `apps/frontend/src/shared/ui/JoinButton.tsx`, tests | 3-4 ч |

**Зависимости от других PR:** нет. Самодостаточен.

**Критический путь:** сам по себе. Можно деплоить независимо от PR #3..#6.

**Pre-deploy чеклист:**
- [ ] `make test` проходит (backend + worker + frontend).
- [ ] `make lint` проходит.
- [ ] `make migrate-test` не нужен (миграций нет).
- [ ] Нет `float`/`Decimal` для денег (грепнуть diff: `rg "Decimal\\(|float\\("`).
- [ ] Middleware не обойден (auth через `request.state.telegram_user`).
- [ ] Нет PII в логах.
- [ ] Frontend bundle проверен на наличие `addEventListener` с конкретными именами
      событий (старый паттерн SSE — не ломается; новая фича не добавляет SSE).

---

## 4. Зависимости от других задач

| Блокирующая | Блокирует |
|---|---|
| PR #1 (deposit on users) | Этот PR (нужен `user.deposit_balance`, `recompute_pause_status`) |
| PR #2 (UI join/wallet) | Этот PR (нужен существующий `MembershipService.join` как baseline) |
| fix LEFT/PAUSED bypass (`ae6bd07`) | Этот PR (его инвариант — deposit-check всегда — сохраняется в `join`, в `subscribe_and_join` явно через `lock_for_update`) |

Новых блокировок этот PR не создаёт.

---

## 5. Ритуал поддержания доков

После успешной реализации — обновить:

| Файл | Что |
|---|---|
| `docs/AGENT_BOOTSTRAP.md` §9 (Что не работает) | удалить пункт «Telegram Payments = мок (webhook-флоу мёртв)» — он закрыт этой фичей на уровне `/subscribe`; оставить «payments на проде всё ещё мок», но endpoint рабочий |
| `docs/02-architecture.md` §6 (Endpoints) | добавить `POST /api/v1/payments/subscribe` со ссылкой на этот план |
| `docs/04-code-standards.md` §11 (Endpoints) | если есть раздел про payments — расширить |
| `docs/06-data-model.md` §3 (Список миграций) | без изменений (миграций нет) |
| `docs/09-prod-readiness.md` §1.1 | snapshot: фича реализована, какой PR, какой endpoint |
| `Pravki-deposit-sse.md` §0 («Контекст и принципы») | добавить ссылку на `Pravki-subscribe-and-join.md` и перечислить Z-12..Z-18 в roadmap |
| `Pravki.md` §3 (Денежные потоки) | если есть раздел про оплату подписки — обновить на новую модель |
| `apps/frontend/docs/STATUS.md` | обновить список экранов: добавить `JoinPayModal`, отметить изменение `JoinButton` |

Каждый коммит — `feat/backend+frontend: ...` с автором `Vegass / dmitriy@vegass.dev`.

Push — только после явного «ок» пользователя.

---

## 6. Что НЕ делаем в этом плане (deferred)

- ❌ **Реальный Telegram Payments** (`send_invoice` от бота, webhook на `/internal/payments/confirm`)
  — отдельная задача (см. `AGENT_BOOTSTRAP.md` §9 «Платежи = мок»).
- ❌ **Продление подписки (`subscription_until` extension)** — отдельная фича (v2).
  В этой фиче `subscription_until` устанавливается на `today + 30d` при первом вступлении
  и не продлевается. Это by design — MVP не предполагает регулярных платежей.
- ❌ **История транзакций для юзера (`GET /me/transactions`)** — отдельная фича.
  В этой фиче `transactions` пишутся для аудита, но UI их не показывает.
- ❌ **Автосписание подписки (cron `close_season` → auto-charge)** — отдельная фича.
- ❌ **Откат подписки (refund)** — отдельная фича.
- ❌ **Webhook от Telegram Payments в реальном времени** — отдельная фича
  (когда подключим `send_invoice`).
- ❌ **Возможность выбора валюты** — MVP только RUB.
- ❌ **Промокоды / скидки на первую подписку** — v2.

---

## 7. Финальный чек-лист перед стартом

- [ ] Прочитан `Pravki-deposit-sse.md` (1494 строки, особенно §0, §Z-2, §Z-3, §Z-4).
- [ ] Прочитан `AGENTS.md` и `PRIVICHKI.md` (правила поведения агента, layered architecture).
- [ ] Прочитан `AGENT_BOOTSTRAP.md` §3 («ДВА .env файла»), §6 («Git и коммиты»), §9
      («Что не работает»), §12 («Ритуал поддержания доков»).
- [ ] Прочитан `docs/04-code-standards.md` (layered architecture, DI, исключения).
- [ ] Подтверждены UX-детали с пользователем:
      - [ ] Кнопка «Вступить» открывает `JoinPayModal` (без изменений текста).
      - [ ] Чекбокс «Согласен на подписку X ₽/мес» обязателен.
      - [ ] Пресеты депозита из `topupPresets.ts` (250/500/750/1000 ₽), фильтрованные по `penalty_amount`.
      - [ ] Кнопка «Оплатить X ₽» (total = price_month + deposit), disabled пока !canPay.
      - [ ] Persistence через БД, не localStorage.
- [ ] `POST /habits/{id}/join` остаётся для реактивации (LEFT/PAUSED→ACTIVE), без изменений.
- [ ] `POST /api/v1/payments/topup` остаётся для пополнения депозита existing members, без изменений.
- [ ] LEFT/PAUSED bypass fix (`ae6bd07`) — сохраняется в `MembershipService.join`,
      явно через `lock_for_update` в `subscribe_and_join`.
- [ ] Transaction(type=SUBSCRIPTION) — один на всю сумму (не два).
- [ ] Idempotency через client-supplied `idempotency_key` (uuid4 из фронта).
- [ ] Server-side gate `subscription_accepted=True` (нельзя обойти чекбокс).
- [ ] Миграций Alembic не требуется.
- [ ] Все существующие тесты остаются зелёными (нет breaking change в `/join`, `/topup`,
      `/me/wallet`).
- [ ] Новые тесты покрывают happy path, idempotency, server-side gate, validation,
      lock-ordering, recompute, frontend UX.
- [ ] Frontend bundle check: `addEventListener` с конкретными именами событий, `onmessage=0`
      (как в `Pravki-deposit-sse.md §Z-6.6`).
- [ ] Доки обновлены (`AGENT_BOOTSTRAP.md` §9, `docs/02-architecture.md` §6,
      `docs/09-prod-readiness.md` §1.1, `Pravki.md` §3, `apps/frontend/docs/STATUS.md`).

---

## 8. Решённые вопросы (snapshot 2026-08-08, после двух итераций ревью)

Все вопросы закрыты. Резолюции интегрированы в план (см. §Z-12.1, §Z-13.1, §Z-13.2,
§Z-13.3, §Z-16, §Z-17).

**Q1 ✅ (принято):** в `JoinPayModal` добавить мелким текстом под чекбоксом подписки
строку вида «Это первый платёж, в следующий раз при повторном открытии нужно будет
пополнить только депозит». Реализация в Z-16 (UI), текст обсудим отдельно если будет
выглядеть громоздко.

**Q2 ✅ (принято после итерации ревью):** backend — единственный источник правды.
`MembershipService.subscribe_and_join` сам разбирает 3 кейса membership-состояния
(см. §Z-13.1 матрица и §Z-13.2 семантика). Frontend адаптирует UI модалки заранее
на основе уже-кешированных `myHabits` (поле `subscription_until` уже есть в
`MembershipOut`):

- Если у юзера нет membership или `subscription_until is None` или `< today` →
  открываем `JoinPayModal` в режиме **«полная оплата»** (с чекбоксом подписки,
  кнопка «Оплатить {sub + deposit} ₽», `subscription_accepted=true` в payload).
- Если у юзера есть membership с `subscription_until >= today` →
  открываем `JoinPayModal` в режиме **«только депозит»** (без чекбокса подписки,
  без строки «Подписка X ₽/мес», кнопка «Пополнить {deposit} ₽ и открыть клуб»,
  `subscription_accepted=false` в payload).

Backend defensive-валидация: даже если frontend ошибся с pre-check (stale cache,
race с deactivation), `subscribe_and_join` шаг 3 + §Z-13.1 матрица всё равно спишет
правильную сумму. `charged_subscription` flag в response покажет, что реально списали.

**Защита от race (в обе стороны даёт меньшее списание — это правильно для юзера):**
- Юзер открыл модалку в режиме «полная оплата», пока `myHabits` был stale. За время
  до клика подписка истекла → backend видит: нет активной подписки,
  `subscription_accepted=true` → списывает sub+deposit. Корректно.
- Обратный race: `myHabits` показывал «нет подписки», а за миллисекунду до клика
  параллельная транзакция создала подписку (через другую вкладку). Backend видит:
  есть активная подписка → списывает только deposit. Корректно (юзер не переплатил).

Оба race'а ведут к «меньшему или равному списанию» чем ожидает frontend → user-friendly.
UI просто покажет alert по `charged_subscription` flag.

**Никакого прямого fallback `/join` в `JoinButton`:** вся логика — в `subscribe_and_join`.
Endpoint `/join` остаётся для технических вызовов (бот, admin, тесты), но UI его не
вызывает напрямую в новой фиче.

**Q3 ✅ (принято):** в `JoinButton` НЕТ прямого фоллбека на `/join`. Вся развилка
(подписка активна / не активна) решается на бэкенде (`subscribe_and_join` шаг 3).
Frontend только адаптирует UI модалки.

**Q4 ✅ (принято):** текст generic-ошибки в `JoinPayModal` — «Не удалось вступить.
Попробуй ещё раз.» Нейтральный, не привязан к конкретной причине (раз всё в одной
атомарной транзакции, частичного списания быть не может, но текст лучше держать общим
на случай будущих ошибок).

**Q5 ✅ (принято):** в `JoinButton` после рефакторинга удалить целиком всю `onError`
ветку и `alert` (после Z-17.3). Компонент становится тривиальным — только открывает
модалку. Ошибки обрабатываются внутри `JoinPayModal.handlePay`.

**Бонус (фикс ревью 2026-08-08):** тип `Transaction` отражает что реально произошло,
а не всегда `SUBSCRIPTION`. Кейс 3b (активная подписка, списываем только депозит)
→ `TransactionType.DEPOSIT_TOPUP` (а не `SUBSCRIPTION`). Семантическая честность данных
с первого дня, защита от путаницы в будущей истории транзакций. Деталь — §Z-13.3.

---

> Все вопросы закрыты, план финализирован. Стартую реализацию в feature-ветке
> `feat/subscribe-and-join` по этому плану.
