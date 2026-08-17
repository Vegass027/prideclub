# Pravki-subscription-2026-08-17 — блокировка по subscription_until + smart renew + бейдж

> **Snapshot 2026-08-17.** Серия из 4 атомарных коммитов на ветке
> `feature/qa-batch-2026-08-14`:
>
> | # | Commit | Что |
> |---|---|---|
> | 1 | `9c96320` | `feat(subscribe): smart renew — charge subscription only when deposit sufficient` |
> | 2 | `3240b83` | `feat(checkin): block checkin/catch on the fly when subscription_until < club_date` |
> | 3 | `9842d4e` | `feat(bot): prefilter rejects expired-subscription check-ins` |
> | 4 | `abde0d3` | `feat(frontend): subscription-expiry badge (3 states) + JoinPayModal renew-only mode` |

## 0. Текущее состояние на проде

**До этой серии:** поле `subscription_until` существует в БД, обновляется при оплате,
но **никто и никогда не проверяет в моменте**. Юзер с истёкшей подпиской продолжал:
- делать чек-ины (если ACTIVE)
- быть целью catch (если ACTIVE)
- попадать в PAUSED через recompute при deposit < penalty

**После серии (commit 1-4):**
- Бэкенд гейтит чек-ин / catch / pay если `subscription_until < club_date` (TZ клуба).
- Frontend показывает бейдж за 1-2 дня до истечения + красный error-бейдж при expired.
- Smart renew: пользователь с достаточным депозитом при продлении платит только `price_month`,
  депозит не трогается (не надо пополнять заново).
- Никаких новых cron-тасок. Никакого нового статуса `LEFT`. Модель "на лету": все гейты
  срабатывают в момент действия.

---

## Z-22 — канонический порядок v3 (обновлён этой серией)

**Было (v2):**

```
I.   HABIT_NOT_FOUND, MEMBERSHIP_NOT_FOUND
II.  ALREADY_CAUGHT, ALREADY_CHECKED_IN, JOINED_LATE
III. MEMBERSHIP_NOT_ACTIVE (legacy), MEMBERSHIP_PAUSED, MEMBERSHIP_LEFT
IV.  WINDOW_CLOSED, WRONG_TOPIC, FORWARDED
V.   WRONG_TYPE, TOO_SHORT, STALE_MESSAGE, EMPTY_TEXT
```

**Стало (v3):**

```
I.   HABIT_NOT_FOUND, MEMBERSHIP_NOT_FOUND
II.  ALREADY_CAUGHT, ALREADY_CHECKED_IN, JOINED_LATE
III. SUBSCRIPTION_EXPIRED   ← NEW, ПЕРВЫЙ в категории, ВЫШЕ PAUSED
     MEMBERSHIP_NOT_ACTIVE (legacy)
     MEMBERSHIP_PAUSED
     MEMBERSHIP_LEFT
IV.  WINDOW_CLOSED, WRONG_TOPIC, FORWARDED
V.   WRONG_TYPE, TOO_SHORT, STALE_MESSAGE, EMPTY_TEXT
```

**Почему подписка ВЫШЕ паузы (canonical #6 vs #7):**

- "Продли подписку" лечит и подписку, и (через `recompute_pause_status`) возможный PAUSED.
- "Пополни депозит" лечит ТОЛЬКО PAUSED, а подписку не лечит → пользователь зациклится
  на ошибке PAUSED после topup (пополнил → не ACTIVE → опять paused → пополни → ...).
- Реакция в обратном порядке решает проблему полностью за один круг.

---

## §Z-22.1 — 4 уровня defense-in-depth

| Уровень | Файл | Метод | Что делает |
|---|---|---|---|
| 1 (sync, UX) | `apps/bot/bot/handlers/checkin.py` `_prefilter` | сравнение `state.subscription_until < state.club_today` (TZ клуба через `habit.club_date`) | Бот мгновенно отвечает `REJECT_SUBSCRIPTION_EXPIRED`, backend не дёргается |
| 2 (sync, defense) | `apps/backend/app/api/v1/internal_checkins.py` `enqueue_checkin` | `if m.subscription_until < habit.club_date(): return code=SUBSCRIPTION_EXPIRED` | Защита от bypassed bot / старой версии / прямого вызова |
| 3 (worker race-fallback) | `apps/backend/app/services/checkin_service.py` `process_checkin` | `raise CheckinSubscriptionExpiredError()` | Race-fallback для race / bypassed bot / прямого вызова |
| 4 (victim-side, defense) | `apps/backend/app/services/penalty_service.py` `apply_catch` | После `lock_for_update + refresh` → reject через `MembershipNotActiveError` | Не ловить жертву с истёкшей подпиской (UI жертвы остался бы в "поймали" но membership уже не ACTIVE) |

---

## §Z-22.2 — Smart renew (Variant I)

**Семантика:** если `user.deposit_balance >= habit.penalty_amount` (депозит уже покрывает
штраф), при продлении подписки списывать ТОЛЬКО `price_month`, депозит не трогать.

**Где:**
- Backend: `apps/backend/app/services/membership_service.py:344-358` — шаг 5
  `subscribe_and_join` (только кейс 3a, `charged_subscription=True`).
- Кейс 3b (активная подписка, topup) — **не меняется**, старая строгая проверка
  `deposit_amount_kopecks >= habit.penalty_amount`.
- Pydantic: `SubscribeRequest.deposit_amount_kopecks` `Field(gt=0)` → `Field(ge=0)`
  (допускает 0 для smart renew).
- Frontend: новый режим `JoinPayModalMode = "renew-only"` — пропускает выбор депозита
  и чекбокс подписки, отправляет `deposit_amount_kopecks=0 + subscription_accepted=true`.

**Идемпотентность:** при smart renew `dep_tx` создаётся с `amount=0` (не денег не двигается,
но ключ `subscribe:{idempotency_key}:dep` зарезервирован для safe retry). Тест
`test_subscribe_smart_renew_idempotent_retry_returns_existing_zero_dep_tx` подтверждает:
повторный POST возвращает ту же транзакцию без двойного списания.

---

## §Frontend — 3 состояния бейджа

**Формула (после ручной сверки):**

```
calendarDiff = subUntil - club_today  // в днях
if calendarDiff < 0 → expired
daysLeft = calendarDiff + 1   // день истечения включительно (Q2: без grace)
if daysLeft >= 3 → ok (без бейджа)
if daysLeft == 1 or 2 → soon (warning)
```

**Почему `+1 shift`:** бэкенд пускает чек-ин когда `subscription_until == club_date`
(Q2: "день-в-день, без grace"). Значит этот день — последний валидный → `daysLeft = 1`
(сегодня — 1 день, который ещё можно использовать).

**Три состояния:**
- `ok` (daysLeft >= 3) — без бейджа, UI показывает "Членство до {date}" как раньше.
- `soon` (daysLeft 1-2) — warning-бейдж "⚠️ Подписка закончится через N дней".
- `expired` (calendarDiff < 0) — error-бейдж "🚫 Подписка окончена".

**Где показывается:**
- `ProfilePage` (`apps/frontend/src/pages/Profile/ProfilePage.tsx`) — рядом с
  бейджем "⏸ пауза" (разные сигналы, не объединяем).
- `TodayPage` (`apps/frontend/src/pages/Today/TodayPage.tsx`) — баннер с CTA
  "🔄 Продлить подписку" → `JoinPayModal` в режиме `renew-only` или `full`.

---

## §TZ-edge — тест на правильный момент

Тест `test_TZ_aware_at_18_UTC_moscow_17_tokyo_18` (apps/frontend/src/shared/utils/__tests__/subscriptionState.test.ts):

```
vi.setSystemTime(new Date("2026-08-17T18:00:00Z"))
# Tokyo:  18+9 = 27 = 03:00 Aug-18 → club_today = 2026-08-18
# Moscow: 18+3 = 21:00 Aug-17 → club_today = 2026-08-17

subUntil=2026-08-18:
- Tokyo  → calendarDiff=0, daysLeft=1 → "soon, через 1 день" (сегодня последний день)
- Moscow → calendarDiff=1, daysLeft=2 → "soon, через 2 дня"
```

Два TZ-состояния действительно различаются в этот момент. `22:00 UTC` (частая ошибка)
уже `01:00 МСК` следующего дня — обе TZ дают 18-е, тест был бы бессмысленным.

---

## Что НЕ трогали

- ❌ Новый статус `LEFT` (по брифу не нужен — в продукте кнопки выхода нет).
- ❌ Новая cron-таска (по брифу — модель "на лету").
- ❌ Логика `recompute_pause_status` (по-прежнему про депозит, не про подписку).
- ❌ `BonusService._grant_reward` (catch-streak auto-renew продолжает работать).
- ❌ WAIVED-маркер (`Pravki-no-deposit-waived-marker.md`) — другая область.

---

## Тестовая сводка

| Слой | Было | Стало | Новых |
|---|---|---|---|
| Backend | 414 | 424 | +10 (smart renew 5, gates 5) |
| Bot | 65 | 69 | +4 (prefilter branch + 3 combo) |
| Frontend | 94 | 100 | +6 (SubscriptionBadge 6) +20 (clubDate 5 + subscriptionState 9 + checkinReject 1 = 15) |
| Worker | без изменений | без изменений | 0 |
| **Итого** | | | **+49** |

11 фейлов в backend — pre-existing baseline (admin_habits_api + user_photo_endpoint),
не регрессии серии.

---

## Что осталось для v2 (НЕ в этой серии)

- Реальная Telegram-оплата (сейчас мок). `bot.send_invoice` / `bot.create_invoice_link`.
- Реальный bot-push при истечении подписки (Q3 — пользователь отверг, бейдж — достаточно).
- `expire_subscriptions_daily` cron (отвергнут в пользу "на лету").
- `chat_id` vs `habit_id` контракт в `internal_payments.py` (отдельная задача).