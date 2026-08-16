# Pravki-no-deposit-waived-marker

> **Статус:** в работе (разведка 2026-08-16, итерация 1). Не готовый документ —
> это living spec для активной задачи. Полная финальная версия появится
> в коммите #5 этого PR.

## Контекст задачи

Юзер-репорт: «окно чек-ина закрыто, депозит пуст, юзер топит депозит,
думает "сейчас отмечусь завтра", но другой юзер заходит, видит жертву
в списке «можно поймать», ловит — деньги списываются, у жертвы депозит
снова пуст, человек в гневе говорит "развод" и уходит».

## Разведка (готово)

См. чат 2026-08-16. Ключевые находки:

1. `apply_window_expired` при `deposit == 0` возвращает `None` ДО любых
   DB-write (penalty_service.py:254-258). В БД не остаётся **никакого
   следа** — ни Penalty, ни Checkin, ни Transaction.
2. `can_catch` в `/members` опирается на `ids_with_any_penalty_today`
   (penalty_repository.py:67-92) — если штрафа за день нет,
   `can_catch=True`.
3. `apply_catch` (penalty_service.py:131-140) проверяет идемпотентность
   только по `reason=CAUGHT` — НЕ ловит отсутствующий WINDOW_CLOSED_NO_CATCH.
4. После topup депозита membership возвращается в ACTIVE через
   `recompute_pause_status` (membership_service.py:147-206) — повторный
   catch технически валиден.
5. **Нет теста** на последовательность «window expired (balance=0) →
   topup → catch». Дыра не закрыта приложением — только отсутствием
   такого сценария в головах пользователей.

## Архитектурное открытие при реализации

**`penalties.reason` — это НЕ Postgres ENUM, а `String(64)` VARCHAR.**
Подтверждение: `app/models/penalty.py:37-39` использует
`mapped_column(String(64), ...)`. Ни одна миграция не делала
`CREATE TYPE penalty_reason`. Единственный реальный ENUM в БД —
`checkin_status` (для `checkins.status`).

Следствие: Alembic-миграция с `ALTER TYPE penalty_reason ADD VALUE` —
**НЕ НУЖНА**. Достаточно добавить значение в Python `StrEnum` —
SQLAlchemy coercion запишет строку `"waived_no_deposit"` в VARCHAR.

Изменение плана: первоначальный commit #1 (миграция + constants.py)
→ **только constants.py**.

## План реализации (4 атомарных коммита + docs пятым)

### Коммит #1: `feat(constants): add PenaltyReason.WAIVED_NO_DEPOSIT`

- **Изменяет:** `apps/backend/app/core/constants.py` (добавить значение
  в `PenaltyReason` enum).
- **Не делает:** ничего больше. Никакой БД-миграции, никакого Alembic.
- **Verification:** `make test` (161 backend-теста зелёные).

### Коммит #2: `feat(penalty): apply_window_expired writes waived marker on deposit=0`

- **Изменяет:** `apps/backend/app/services/penalty_service.py`,
  функция `apply_window_expired`, ветка `amount <= 0` (строки 254-258).
- Вместо `return None` — создать `Penalty(reason=WAIVED_NO_DEPOSIT,
  amount=0, fund_share=0, ...)`, `flush()`, вернуть `None`.
- **НЕ** вызывать `checkin_repo.upsert_status(...)` (Checkin остаётся None).
- **НЕ** создавать `Transaction(amount=0)`.
- **НЕ** вызывать `recompute_pause_status` (баланс не менялся).
- **Verification:** новые unit-тесты `apply_window_expired_writes_waived_marker_when_deposit_zero`,
  `apply_window_expired_idempotent_after_waived_marker`.

### Коммит #3: `feat(penalty): apply_catch idempotency covers all penalty reasons`

- **Изменяет:** `apps/backend/app/services/penalty_service.py`,
  `apply_catch`, проверка идемпотентности (строки 131-140).
- Убрать фильтр `reason == CAUGHT` — проверять любой `Penalty` за
  `(membership_id, date)`.
- Единый код ошибки: `PenaltyAlreadyProcessedError()` → `code="penalty_already_processed"`.
- **Бонус — закрывает дополнительную дыру:** прямой POST /catch поверх
  существующего `WINDOW_CLOSED_NO_CATCH` за день теперь корректно
  отвергается. Раньше UNIQUE `uq_penalty_per_day_reason` пропускал
  (reason отличался), позволяя двойное списание в обход UI.
- **Verification:** unit-тесты `apply_catch_rejected_when_waived_marker_exists`,
  `apply_catch_rejected_when_window_closed_penalty_exists`,
  `apply_catch_rejected_when_existing_caught_penalty` (регрессия),
  `apply_catch_succeeds_for_other_date`.

### Коммит #4: `refactor(worker): remove duplicated apply_window_expired call in close_catch_window`

- **Изменяет:** `apps/worker/worker/tasks/close_catch_window.py:74-82`.
- Мёртвый дубль второго вызова `apply_window_expired` после `return None`
  / `continue`. Unreachable code (если penalty is not None — `continue`;
  если None — второй вызов идемпотентно вернёт None).
- **Verification:** worker-тесты 34 шт. зелёные.

### Коммит #5: `docs: Pravki-no-deposit-waived-marker`

- Обновить `docs/06-data-model.md` §3 (PenaltyReason values).
- Обновить `docs/04-code-standards.md` §7 (идемпотентность штрафов).
- Обновить `docs/09-prod-readiness.md` §1.1 (что починено).
- Развернуть этот файл в финальную версию (snapshot, тестовая матрица,
  prod-verify шаги).

## Backlog (отдельные задачи, НЕ в этом PR)

### `BL-001`: CHECK constraint на `penalties.reason`

Defense-in-depth на уровне схемы БД — сейчас `reason` VARCHAR без
constraint, можно теоретически записать произвольную строку.
Зафиксировать валидные значения:

```sql
ALTER TABLE penalties 
ADD CONSTRAINT chk_penalty_reason 
CHECK (reason IN ('caught', 'window_closed_no_catch', 'waived_no_deposit'));
```

Перед применением на проде: `SELECT DISTINCT reason FROM penalties;` —
убедиться, что нет «мусорных» значений. По разведке — только два
известных значения, должно пройти чисто.

Это **отдельная задача** «укрепить схему БД», не блокирует финансовый
фикс. Делать в отдельном PR.

### `BL-002`: UI-различие «штраф списан» vs «штраф прощён»

Сейчас `TodayPage` показывает «штраф не списан» и для WAIVED, и для
«день ещё не наступил как missed». Если захотим показать
«Прощён (депозит был пуст)» явно — расширить `TodayResponse` полем
`penalty_outcome: 'charged' | 'waived_no_deposit' | 'none'`, добавить
text-key в `shared/texts/`. Дизайн-решение требуется.

### `BL-003`: расширить `CheckinEvent.payload` полем `checkin_status`

Известная проблема (см. `docs/09-prod-readiness.md` §1.1): бот и
мини-апп показывают РАЗНЫЕ формулировки для `caught_today` (cron
`apply_window_expired` vs `apply_catch`). После нашего фикса эта
разница сохранится — нужно расширить `_publish_checkin_rejected` и
маппер на фронте. Отдельный PR.

### `BL-004`: Audit-trail для «кто первый закрыл окно для юзера»

Потенциально полезный observability-сигнал — при WAIVED_NO_DEPOSIT
записывать в `audit_log` (если появится) или отдельную таблицу
`window_expired_waived(user_id, habit_id, club_date, deposit_balance_at_close)`.
Может пригодиться для антифрод-эвентов или продуктовой аналитики
«сколько юзеров в принципе не могут позволить штраф».

## История решений

- 2026-08-16: разведка подтвердила наличие дыры.
- 2026-08-16: юзер выбрал Вариант A (минимальный — маркерный Penalty),
  не B (CHECK constraint).
- 2026-08-16: в процессе реализации выяснилось, что `penalties.reason`
  — это VARCHAR, не ENUM. Миграция Alembic не нужна, изменён план
  коммита #1.