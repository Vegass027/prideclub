# Habit Club — Execution Plan / 2026-08-19

> **Этот документ — ТОЧКА ВХОДА для работы.** Пошаговый план задач от сложных/нужных до мелких/техдолга.
> Цель: после выполнения всех задач — **полноценный рабочий продукт**, на который можно наслаивать новые фичи.
> **Версия:** 1.0. **Дата:** 2026-08-19. **Автор:** AI-ассистент по запросу Дмитрия.
>
> **Контекст:**
> - HEAD = `2dd596c` (локально), origin = `c6647b7` (нужен push)
> - Прод: 2 users, 3 habits, 3 memberships, 4 transactions (snapshot 2026-08-09)
> - 15 production-серий задеплоены (см. `STATUS-2026-08-19.md §2.1`)
> - 0 живых пользователей, 0₽ в обороте → цена ошибки низкая, время на рефакторинг есть
>
> **Источники ТЗ:**
> - `TZ_kharakteristiki_personazha.md` — Фаза B (характеристики персонажа)
> - `4_finansovaya_mehanika_shtrafov_i_prizov.md` — финансовая модель
> - `prideclub_karta_proekta.md` — общая карта проекта
> - `Pravki-business-logic-recon-2026-08-18.md` — 28 находок (gap-анализ)

---

## 0. TL;DR

| Фаза | Что | Задач | Время | Блокирует прод? |
|---|---|---|---|---|
| **0** | Закрыть финансовую дыру | 1 | 30 мин | нет (0₽ в обороте), но при первом юзере стрельнёт |
| **1** | Bonus wiring (`apply_catch_bonus`) | 3 | 1-2 дня | нет, но лидерборд мёртвый |
| **2** | Призовой фонд (seasons enable) | 4 | 1 неделя | нет (сезонов нет) |
| **3** | Character & Stats (Фаза B из TZ) | 11 | 2-3 недели | нет, но это центральная фича ТЗ |
| **4** | Frontend (страницы + персонаж) | 10 | 3-5 дней | да — без UI продукт не работает |
| **5** | Техдолг (admin, hardening) | 6 | 1-2 дня | нет |
| **6** | Deploy & Production | 4 | по 1 дню | нет (для soft-launch) |
| **7** | Growth (партнёрка, рефералка) | 3 | 2-3 недели | нет, но без роста нет пользователей |

**После всех 7 фаз (5-6 недель) — полноценный рабочий продукт.**

---

## 1. Что ЗАДУМАНО (MVP-критерии полноценного продукта)

Из `TZ_kharakteristiki_personazha.md` + `prideclub_karta_proekta.md` + `4_finansovaya_mehanika_shtrafov_i_prizov.md`:

### 1.1 Core habit loop
1. ✅ Юзер вступает в клуб (`POST /api/v1/habits/{id}/join` через Mini App)
2. ✅ Платит подписку + депозит одним платежом (`POST /api/v1/payments/subscribe`)
3. ✅ Делает чек-ин с доказательством (видео-кружок / фото / текст) через бот
4. ✅ При пропуске — штраф в общий призовой фонд клуба (`Habit.prize_pool`)
5. ✅ Другой участник может «поймать» нарушителя за бонус (`POST /api/v1/members/{id}/catch`)
6. ❌ В конце сезона — топ-5 получают призы (5/250/20/12/8% от фонда) — **НЕ РАБОТАЕТ** (см. #2)
7. ❌ Лидерборд по очкам ловцов — **НЕ РАБОТАЕТ** (см. #1)

### 1.2 Геймификация (Фаза B)
1. ❌ Характеристика растёт при чек-ине, падает при штрафе (отдельная ось, не рубли)
2. ❌ Сумма всех характеристик → глобальный статус (Новичок → Практик → Мастер → Легенда)
3. ❌ Заморозка характеристики после 30 дней без чек-ина
4. ❌ Лидерборд по характеристике внутри клуба
5. ❌ Экран «Мой персонаж» с карточками статуса

### 1.3 Frontend
- ❌ Marketplace, Today, Members, Balance, Leaderboard, Profile, Onboarding — **API endpoints есть, страницы не подключены**

### 1.4 Финансы (по `4_finansovaya_mehanika`)
- Вход 1000₽/мес, штраф 250₽, депозит 750-1000₽
- 5 призовых мест (35/25/20/12/8%)
- Lava.top / Tribute для выплат
- Налоги: < 4000₽/год на человека = без декларации

---

## 2. Что УЖЕ реализовано (на проде, HEAD c6647b7)

| # | Подсистема | Где | Комментарий |
|---|---|---|---|
| 1 | Auth (initData + JWT /internal) | `core/middleware.py`, `packages/shared/security.py` | 161 тестов, E2E |
| 2 | Чек-ины (Celery worker) | `worker/tasks/process_checkin.py` | идемпотентность по `(membership_id, date)` |
| 3 | Кэтчер (`apply_catch`) | `services/penalty_service.py:78-256` | race-free после Z-2, `CatchWindowClosedError` |
| 4 | Депозит (`users.deposit_balance`) | миграции 014a/014b, `services/membership_service.py` | `recompute_pause_status` |
| 5 | Платежи (subscribe_and_join) | `services/membership_service.py:300-450`, `POST /api/v1/payments/subscribe` | 3a/3b/3c кейсы, smart renew |
| 6 | Bot pre-filter | `bot/handlers/checkin.py` | 5-round fix (Z-22), canonical priority v2 |
| 7 | SSE real-time | `redis_stream_bus.py`, `useTodayStream` hook | 6-step, JWT-токен |
| 8 | Admin Mini App | `admin.prideclub.fun`, `apps/frontend/src/admin/` | owner-gate, CRUD + activate/archive/restore/permanent-delete |
| 9 | Topic-scoped чек-ины | миграции 010/011, `bot/handlers/checkin.py` | 3 топика (checkin/notifications/chat) |
| 10 | Multi-proof_types | миграция 012 | 1-3 типа чек-ина на клуб |
| 11 | Subscription gating | Z-22, Pravki-subscription-2026-08-17 | `subscription_until` блокирует чек-ин |
| 12 | WAIVED-маркер для PAUSED | Z-19, Pravki-no-deposit-waived-marker | `mark_waived_unable_to_pay` |
| 13 | **Manual catch (Z-1/Z-2/Z-3)** | `f4eb243`+`48210e9`+`1b1d325`+`3b81327` | авто-списание отключено, ручная поимка только |
| 14 | Joinee-late protection | Z-19, миграция 015 | 3 уровня defense-in-depth |
| 15 | Pre-filter 5-round fix | Z-22, 7 коммитов | `caught_today + window_closed` инверсия, drift-test |

**Тесты:** 384 backend + 77 worker + 40 bot + 68 frontend (с pre-existing fails, не блокеры).

**Подробности по каждой серии — `STATUS-2026-08-19.md §2.1`.**

---

## 3. Что НЕ реализовано (главный gap)

| # | Что | Severity | Источник |
|---|---|---|---|
| A | **#17** `apply_catch` deposit=0 без WAIVED-маркера (прямая финансовая) | 🔴 | recon |
| B | **#1** `apply_catch_bonus` не вызывается в проде (лидерборд мёртвый) | 🟠 | recon |
| C | **#2** `Season.prize_pool` не пишется (призовой фонд не распределяется) | 🟠 | recon |
| D | **Фаза B (TZ):** user_stats, user_statuses, increment/decrement, freeze worker, эндпоинты, frontend | 🟠 | TZ |
| E | Frontend не подключен к API (7 страниц) | 🟠 | prod-readiness §2.3 |
| F | Admin техдолг (TD-1..TD-4) | 🟡 | recon |
| G | 9 устаревших production-комментариев | 🟢 | prod-readiness §3 |

**Всё разложено по фазам ниже.**

---

# Фаза 0 — Закрыть финансовую дыру (30 мин)

## Task 0.1: fix #17 — `apply_catch` deposit=0 пишет WAIVED-маркер

**Приоритет:** 🔴 Critical (но 0₽ в обороте, не стреляет сейчас — стрельнёт на первом юзере).
**Время:** 30 мин (1 коммит + тест).
**Зависимости:** нет.
**Блокирует:** Фазу 1 (бонус-wiring) — иначе в тестах WAIVED-маркер не будет работать.

### Что сделать

**Файл:** `apps/backend/app/services/penalty_service.py:171-177`

Текущий код:
```python
amount = min(habit.penalty_amount, violator_user.deposit_balance)
if amount <= 0:
    raise PenaltyAlreadyProcessedError("deposit_exhausted", code="deposit_exhausted")
```

Заменить на:
```python
amount = min(habit.penalty_amount, violator_user.deposit_balance)
if amount <= 0:
    # WAIVED-маркер для справедливости (см. recon #17):
    # без него после topup apply_catch спишет деньги за прошлый день.
    # Паттерн: см. apply_window_expired WAIVED-ветку в Pravki-no-deposit-waived-marker.
    waived = Penalty(
        user_id=violator_user.id,
        habit_id=habit.id,
        membership_id=violator_membership_id,
        catcher_id=None,
        amount=0,
        reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
        club_date=club_date,
        idempotency_key=f"waived:{violator_membership_id}:{club_date}",
    )
    self._session.add(waived)
    await self._session.flush()
    raise PenaltyAlreadyProcessedError(
        "deposit_exhausted",
        code="deposit_exhausted",
        waived_marker_id=str(waived.id),
    )
```

### Критерий «готово»

- [ ] Юнит-тест `test_apply_catch_deposit_zero_writes_waived_marker`:
  - Юзер ACTIVE + deposit=0
  - apply_catch → бросает `PenaltyAlreadyProcessedError("deposit_exhausted")` + в БД появился `Penalty(reason=WAIVED_UNABLE_TO_PAY, amount=0)`
  - UNIQUE на `(membership_id, date, reason)` не нарушается (idempotency_key)
- [ ] Юнит-тест `test_apply_catch_deposit_zero_then_topup_no_double_charge`:
  - Юзер ACTIVE + deposit=0 + WAIVED-маркер за вчера
  - topup → deposit > 0
  - apply_catch за вчера → existing Penalty check отвергает (no double charge)
- [ ] `make test` зелёный (все 384 + новые 2)
- [ ] `make lint` чистый

### Запуск
```bash
# Локально
git checkout -b fix/apply-catch-deposit-zero-waived
# ... правка ...
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev commit -am "fix(penalty): apply_catch deposit=0 writes WAIVED marker (#17)"
make test
# Push + deploy (по отдельному "ок" пользователя)
```

---

# Фаза 1 — Bonus wiring (Sprint 1, 1-2 дня)

**Цель:** ловцы получают обещанные бонусные баллы. Лидерборд «Охотники» начинает работать.

## Task 1.1: зарегистрировать `apply_catch_bonus` в `_TASK_NAMES`

**Приоритет:** 🟠 High (лидерборд мёртвый).
**Время:** 5 мин.
**Зависимости:** нет.
**Файл:** `apps/backend/app/services/celery_producer.py:21-35`

### Что сделать

Добавить в `_TASK_NAMES`:
```python
_TASK_NAMES: dict[str, str] = {
    "checkin": "worker.tasks.process_checkin.run",
    "penalty": "worker.tasks.process_penalty.run",
    "payment": "worker.tasks.process_payment.run",
    "publish_catch_event": "worker.tasks.publish_catch_event.run",
    "publish_you_were_caught": "worker.tasks.publish_you_were_caught.run",
    "apply_catch_bonus": "worker.tasks.apply_catch_bonus.run",  # ← ДОБАВИТЬ
}
```

### Критерий «готово»
- [ ] Импорт `worker.tasks.apply_catch_bonus` не падает (worker этот task уже существует)
- [ ] `send_task("apply_catch_bonus", {...})` корректно сериализует имя

## Task 1.2: `send_task("apply_catch_bonus", ...)` в `process_penalty`

**Файл:** `apps/worker/worker/tasks/process_penalty.py` (после успешного `apply_catch`).

### Что сделать

В прод-обёртке `run()` после успешного `apply_catch` (там где уже есть `publish_catch_event` / `publish_you_were_caught`):
```python
# После apply_catch успеха:
try:
    celery_producer.send_task(
        "apply_catch_bonus",
        payload={
            "catcher_membership_id": str(catcher_membership_id),
            "violator_membership_id": str(violator_membership_id),
            "penalty_id": str(penalty_id),
        },
    )
    log.info("catch_bonus_dispatched", extra={...})
except Exception as exc:
    log.exception("catch_bonus_dispatch_failed", extra={"err": str(exc)})
    # НЕ raise — основной поток не должен ломаться
```

**Важно:** отдельный try/except (как для `publish_catch_event` / `publish_you_were_caught`), чтобы сбой bonus-таска не ломал основной поток.

### Критерий «готово»
- [ ] После успешного `apply_catch` в логах worker видно `catch_bonus_dispatched`
- [ ] При сбое bonus-таска остальной поток (`publish_*`) не ломается

## Task 1.3: e2e-тест через broker (НЕ прямой импорт!)

**Файл:** новый `apps/worker/tests/test_apply_catch_bonus_e2e.py`

### Что сделать

Тест должен **НЕ** вызывать `_process` напрямую, а реально проходить через broker:
```python
@pytest.mark.asyncio
async def test_apply_catch_bonus_dispatched_via_broker():
    """Bonus начисляется через broker, не через прямой вызов."""
    # 1. Запустить celery_app.apply_async с task_name="apply_catch_bonus"
    # 2. Дождаться результата (через result.get(timeout=5))
    # 3. Проверить: User.bonus_points += 1, Transaction(type=BONUS_CATCH) создан
```

**Подробный паттерн:** см. `apps/worker/tests/test_worker_cron_chain.py` — но **через broker**, не через `_process`.

### Критерий «готово»
- [ ] Тест проходит локально
- [ ] Тест **падает** если закомментировать `send_task` в `process_penalty` (регрессия)
- [ ] CI зелёный

---

# Фаза 2 — Призовой фонд (Sprint 4, 1 неделя)

**Цель:** `close_season` распределяет 5 призовых мест (35/25/20/12/8%) из `Habit.prize_pool`.

## Task 2.1: snapshot `Habit.prize_pool` → `Season.prize_pool`

**Приоритет:** 🟠 High.
**Время:** 2-3 часа.
**Файл:** `apps/backend/app/services/penalty_service.py:180` + `apps/backend/app/services/season_service.py`

### Что сделать

В `apply_catch` (после `add_to_prize_pool`) — **дополнительно** инкрементить `Season.prize_pool` текущего активного сезона:
```python
# В apply_catch после add_to_prize_pool:
active_season = await self._season_repo.get_active_for_habit(habit.id, club_date)
if active_season is not None:
    await self._season_repo.add_to_prize_pool(active_season.id, amount)
```

### Что нужно сначала
- Метод `SeasonRepository.get_active_for_habit(habit_id, club_date)` — найти сезон, у которого `status='active' AND habit_id=:id AND start_at <= :club_date AND end_at >= :club_date`
- Метод `SeasonRepository.add_to_prize_pool(season_id, amount)` — атомарный инкремент под `FOR UPDATE`

### Критерий «готово»
- [ ] Юнит-тест: `apply_catch` инкрементит и `Habit.prize_pool`, и `Season.prize_pool` (если есть активный сезон)
- [ ] Юнит-тест: если активного сезона нет — `Habit.prize_pool` всё равно инкрементится
- [ ] `SELECT FOR UPDATE` на Season row

## Task 2.2: admin endpoint `POST /admin/v1/habits/{id}/seasons`

**Файл:** новый роут в `apps/backend/app/api/admin/v1/seasons.py` (или добавить в `habits.py`)

### Что сделать
```python
class AdminSeasonCreateRequest(BaseModel):
    title: str
    start_at: date
    end_at: date
    prize_pool_initial: int = 0  # опционально
    prize_rules: list[dict]  # [{"place": 1, "percentage_bp": 3500}, ...]

@router.post("/admin/v1/habits/{habit_id}/seasons", response_model=AdminSeasonOut)
async def create_season(habit_id: str, payload: AdminSeasonCreateRequest, ...):
    return await HabitService.create_season(habit_id, **payload.dict())
```

### Что нужно в `HabitService.create_season`:
- Валидация: `start_at < end_at`, `prize_rules` сумма = 100%
- Создание `Season(status='active' or 'planned', ...)`
- Снимок `prize_rules` в `Season.prize_rules JSONB` (чтобы правила не менялись задним числом)

### Критерий «готово»
- [ ] POST создаёт Season в БД
- [ ] GET `/admin/v1/habits/{id}/seasons` возвращает список
- [ ] Admin Mini App UI: форма создания сезона (опционально, можно позже)

## Task 2.3: `close_season` распределяет по 5 местам (35/25/20/12/8%)

**Файл:** `apps/backend/app/services/season_service.py:60-122`

### Что сделать

Заменить `BASIS_POINTS_TOTAL = 10_000` на **дефолтные 5 правил**:
```python
DEFAULT_PRIZE_RULES = [
    {"place": 1, "percentage_bp": 3500},  # 35%
    {"place": 2, "percentage_bp": 2500},  # 25%
    {"place": 3, "percentage_bp": 2000},  # 20%
    {"place": 4, "percentage_bp": 1200},  # 12%
    {"place": 5, "percentage_bp":  800},  # 8%
]
# Сумма = 10000 bp = 100% (без остатка)
```

Использовать `season.prize_rules` (если заданы в Task 2.2), иначе `DEFAULT_PRIZE_RULES`.

### Критерий «готово»
- [ ] Юнит-тест: `close_season` для фонда 15 000₽ → 1 место 5250₽, 2 место 3750₽, 3 место 3000₽, 4 место 1800₽, 5 место 1200₽
- [ ] Юнит-тест: пустой фонд → 0 выплат (или по сценарию rollover)
- [ ] `Transaction(type=PRIZE)` создаётся для каждого победителя

## Task 2.4: e2e для seasons через broker

**Файл:** `apps/worker/tests/test_close_season_e2e.py`

### Что сделать
- Создать Season, заполнить фонд через `apply_catch`, запустить `close_season.run` через broker
- Проверить: 5 транзакций, правильные суммы

### Критерий «готово»
- [ ] Тест проходит через broker (НЕ прямой вызов `_process`)
- [ ] CI зелёный

---

# Фаза 3 — Character & Stats (Фаза B из TZ, 2-3 недели)

**Цель:** характеристика растёт/падает, глобальный статус, лидерборд по характеристике, заморозка.

**Важно:** `apps/backend/alembic/versions/009_chat_id_partial_unique.py` уже существует → **миграция для `user_statuses` seed будет 016**, не 009. Учесть в именовании.

## Task 3.1: модель `UserStats`

**Файл:** новый `apps/backend/app/models/user_stats.py`

### Что сделать

```python
class UserStats(Base):
    __tablename__ = "user_stats"
    id: UUID (PK)
    user_id: BIGINT (FK → users.id)
    habit_id: UUID (FK → habits.id)
    value: BIGINT (default 0, CHECK >= 0)
    last_checkin_at: TIMESTAMPTZ (nullable)
    is_frozen: BOOLEAN (default false)
    frozen_at: TIMESTAMPTZ (nullable)
    frozen_reason_text: VARCHAR(256) (default 'Отказался расти дальше')
    created_at, updated_at: TIMESTAMPTZ

    __table_args__ = (
        UniqueConstraint("user_id", "habit_id", name="uq_user_stats_user_habit"),
        CheckConstraint("value >= 0"),
        CheckConstraint("(is_frozen = false AND frozen_at IS NULL) OR (is_frozen = true AND frozen_at IS NOT NULL)"),
        Index("ix_user_stats_user", "user_id"),
        Index("ix_user_stats_habit_value", "habit_id", "value"),
        Index("ix_user_stats_freeze_cron", "is_frozen", "last_checkin_at", postgresql_where=text("is_frozen = false")),
    )
```

### Критерий «готово»
- [ ] Модель импортируется без ошибок
- [ ] `relationship` в `User.stats` и `Habit.stats`

## Task 3.2: модель `UserStatus` (справочник)

**Файл:** новый `apps/backend/app/models/user_status.py`

```python
class UserStatus(Base):
    __tablename__ = "user_statuses"
    id: UUID (PK)
    status_name: VARCHAR(64)
    min_threshold: INTEGER (CHECK >= 0)
    icon_url: VARCHAR(512)
    sort_order: INTEGER (UNIQUE)
```

## Task 3.3: миграция 016 (seed `user_statuses`)

**Файл:** новый `apps/backend/alembic/versions/016_user_statuses_seed.py` (revises `015`)

### Что сделать

Seed-данные:
| status_name | min_threshold | sort_order | icon_url |
|---|---|---|---|
| Новичок | 0 | 1 | /badges/newbie.svg |
| Практик | 30 | 2 | /badges/practitioner.svg |
| Мастер | 150 | 3 | /badges/master.svg |
| Легенда | 500 | 4 | /badges/legend.svg |

```python
def upgrade():
    op.bulk_insert(user_statuses_table, [
        {"status_name": "Новичок", "min_threshold": 0, "sort_order": 1, ...},
        ...
    ])
```

### Критерий «готово»
- [ ] `make migrate-test` (upgrade head → downgrade base → upgrade head) проходит
- [ ] `SELECT * FROM user_statuses` на проде даёт 4 строки

## Task 3.4: `UserStatsRepository` + `UserStatusRepository`

**Файлы:** новые `apps/backend/app/repositories/user_stats_repository.py` + `user_status_repository.py`

### Методы `UserStatsRepository`:
- `get_or_create_for_update(user_id, habit_id) -> UserStats` (под `FOR UPDATE`)
- `increment(user_id, habit_id, delta: int)` — `value += delta, last_checkin_at=NOW`
- `decrement_floored(user_id, habit_id, delta: int) -> int` — `value = GREATEST(0, value - delta)`, возвращает фактический декремент
- `unfreeze(user_id, habit_id)`
- `iter_for_freeze_cron(days_inactive: int) -> Iterable[UserStats]`
- `iter_for_leaderboard(habit_id, limit: int) -> Iterable[(UserStats, User, Membership)]`

### Методы `UserStatusRepository`:
- `get_by_threshold(value: int) -> UserStatus | None` — `MAX(min_threshold) WHERE min_threshold <= :value`
- `get_next_threshold(value: int) -> UserStatus | None` — `min(min_threshold) WHERE min_threshold > :value`

### Критерий «готово»
- [ ] DI через конструктор (никаких `self._session = AsyncSession()` внутри)
- [ ] Все методы с `async`
- [ ] `lock_for_update` в `get_or_create_for_update`

## Task 3.5: `CharacterConfig` в `core/constants.py`

**Файл:** `apps/backend/app/core/constants.py`

```python
class CharacterConfig:
    """Конфиг механики 'Персонаж и характеристики'."""
    DEFAULT_STAT_GAIN_PER_CHECKIN = 2
    DEFAULT_STAT_LOSS_PER_MISS = 1
    FREEZE_AFTER_DAYS_INACTIVE = 30
    DEFAULT_FROZEN_REASON = "Отказался расти дальше"
    MIN_TOTAL_VALUE_TO_SHOW = 1
    FREEZE_CRON_HOUR_UTC = 4
    FREEZE_CRON_BATCH_SIZE = 1000
```

### Критерий «готово»
- [ ] `from app.core.constants import CharacterConfig` работает
- [ ] Нет магических чисел в сервисах/роутах — везде через `CharacterConfig`

## Task 3.6: `CharacterService`

**Файл:** новый `apps/backend/app/services/character_service.py`

### Методы:
- `get_character(user_id) -> CharacterOut` (для `GET /character/me`)
- `get_leaderboard(habit_id, limit=20) -> list[LeaderboardEntry]`
- `increment_on_checkin(user_id, habit_id)` — вызывается из `CheckinService.process_checkin`
- `decrement_on_penalty(user_id, habit_id)` — вызывается из `PenaltyService.apply_catch`
- `apply_freeze(user_stats_id, reason)` — для worker

### DI:
```python
class CharacterService:
    def __init__(self, session, user_stats_repo, user_status_repo):
        ...
```

### Критерий «готово»
- [ ] `increment_on_checkin` создаёт `UserStats` если нет, иначе инкрементит
- [ ] `increment_on_checkin` **размораживает `is_frozen`** (см. ниже)
- [ ] `decrement_on_penalty` не уходит в минус (`GREATEST(0, value - delta)`)

### Возврат из заморозки (важно!)

Когда юзер возвращается после 30 дней без чек-ина:

```python
# CharacterService.increment_on_checkin (Task 3.6)
async def increment_on_checkin(self, user_id: int, habit_id: str) -> UserStats:
    stats = await self._user_stats_repo.get_or_create_for_update(user_id, habit_id)
    if stats.is_frozen:
        # Юзер вернулся после паузы — размораживаем.
        stats.is_frozen = False
        stats.frozen_at = None
        # frozen_reason_text НЕ очищаем — это история, полезна для UI ("вернулся после 30 дней")
    stats.value += habit.stat_gain_per_checkin
    stats.last_checkin_at = datetime.now(tz=UTC)
    return stats
```

**Важно:** `is_frozen` сбрасывается ТОЛЬКО при успешном чек-ине в ЭТОМ клубе. Юзер
может продолжать делать чек-ины в ДРУГИХ клубах — заморозка в "забытом" клубе
остаётся до тех пор, пока юзер не вернётся в ЭТОТ клуб.

**Важно:** `is_frozen` НЕ блокирует чек-ин (в отличие от `subscription_expired` /
`membership_paused` / `membership_left`). Чек-ин проходит по обычным правилам;
`is_frozen` — только визуальный маркер + способ пропустить штрафы/бонусы
(значение не менялось 30 дней, логично что нечего декрементить за "эту" неделю).

### Сценарий "вернуться из заморозки" (по шагам)

```
Шаг 0: Юзер в клубе, is_frozen=true (30+ дней без чек-ина)

Шаг 1: Юзер открывает Mini App
        → GET /api/v1/character/me
        → видит баннер "Характеристика заморожена" (Task 4.11)
        → видит "subscription_until: 2026-09-05" (может истекла)

Шаг 2: Юзер отправляет боту видео-кружок
        Bot prefilter проверяет (по canonical priority v2):
        - subscription_expired? → "продли подписку"
        - membership_paused? (deposit=0) → "пополни депозит"
        - window_closed? → "окно закрыто, жди завтра"
        - Всё ОК → "Принято!"

Шаг 3: Worker CheckinService.process_checkin:
        - Проверяет canonical #6, #7, #8 — все ОК
        - INSERT checkin
        - (после Фазы 3) CharacterService.increment_on_checkin:
          * get_or_create_for_update → existing UserStats с is_frozen=true
          * is_frozen = false, frozen_at = None
          * value += stat_gain_per_checkin
          * last_checkin_at = NOW()

Шаг 4: UserStats снова в активном состоянии
        Баннер "заморожено" исчезает в Mini App
```

**Edge case:** если подписка истекла + депозит = 0 + is_frozen=true — три
независимых блока нужно устранить (продлить подписку, пополнить депозит, сделать
чек-ин). UX в Mini App должен показывать **список всех блоков** разом (чтобы
юзер не исправлял по одному).

## Task 3.7: интеграция в `CheckinService.process_checkin`

**Файл:** `apps/backend/app/services/checkin_service.py:51-128`

### Что сделать

После успешного `INSERT INTO checkins` (когда `created=True`):
```python
if created:
    # Инкремент характеристики (Фаза B)
    await self._character_service.increment_on_checkin(
        user_id=user_id,
        habit_id=habit_id,
    )
```

### Критерий «готово»
- [ ] В одной транзакции с `INSERT checkin` происходит `increment_on_checkin`
- [ ] При откате транзакции — оба откатываются
- [ ] `created=False` (повторный чек-ин за день) — **НЕ** инкрементит

## Task 3.8: интеграция в `PenaltyService.apply_catch`

**Файл:** `apps/backend/app/services/penalty_service.py:65-179`

### Что сделать

После `add_to_prize_pool` (в той же транзакции):
```python
# Декремент характеристики нарушителя
await self._character_service.decrement_on_penalty(
    user_id=violator_user_id,
    habit_id=habit_id,
)
```

### Критерий «готово»
- [ ] `apply_catch` декрементит `UserStats.value` для нарушителя
- [ ] `apply_catch` НЕ декрементирует ловца (это отдельная ось)
- [ ] `suspicious_pairs` не влияет на декремент (как в TZ §3.3 — «Дисциплина не ослабляется»)

## Task 3.9: worker `freeze_inactive_stats`

**Файлы:** новый `apps/worker/worker/tasks/freeze_inactive_stats.py` + регистрация в `celery_app.py`

### Что сделать

```python
@async_task(name="worker.tasks.freeze_inactive_stats.run")
async def run() -> dict:
    cutoff = datetime.now(tz=UTC) - timedelta(days=CharacterConfig.FREEZE_AFTER_DAYS_INACTIVE)
    repo = UserStatsRepository(session)
    candidates = repo.iter_for_freeze_cron(days_inactive=CharacterConfig.FREEZE_AFTER_DAYS_INACTIVE)
    frozen_count = 0
    for stats in islice(candidates, CharacterConfig.FREEZE_CRON_BATCH_SIZE):
        await character_service.apply_freeze(stats.id, CharacterConfig.DEFAULT_FROZEN_REASON)
        frozen_count += 1
    return {"frozen": frozen_count, "cutoff": cutoff.isoformat()}
```

В `celery_app.py`:
```python
"freeze_inactive_stats_daily": {
    "task": "worker.tasks.freeze_inactive_stats.run",
    "schedule": crontab(hour=CharacterConfig.FREEZE_CRON_HOUR_UTC, minute=0),
},
```

### Критерий «готово»
- [ ] Cron зарегистрирован в `celery_app.conf.beat_schedule`
- [ ] Юнит-тест: `UserStats` с `last_checkin_at = 31 days ago` → `is_frozen=True`
- [ ] Идемпотентность: повторный запуск → 0 изменений

## Task 3.10: эндпоинт `GET /api/v1/character/me`

**Файл:** новый `apps/backend/app/api/v1/character.py`

### Контракт (из TZ §3.5):
```json
{
  "total_value": 142,
  "status": {"name": "Практик", "icon_url": "...", "next_threshold": 150, "next_status": "Мастер"},
  "stats": [
    {"habit_id": "...", "habit_title": "Планка 30 мин", "stat_name": "Эстетика тела",
     "stat_icon": "💪", "value": 58, "is_frozen": false, "frozen_reason_text": null,
     "last_checkin_at": "2026-07-21T05:14:00Z"}
  ]
}
```

### Критерий «готово»
- [ ] Эндпоинт требует initData auth
- [ ] Возвращает все `UserStats` юзера с `habit_title`, `stat_name`
- [ ] `total_value = SUM(value) WHERE user_id = :user_id`
- [ ] `status` = `UserStatus` где `min_threshold <= total_value` (MAX)
- [ ] `next_status` = следующий `UserStatus` (если есть)

## Task 3.11: эндпоинт `GET /api/v1/leaderboard/stat?habit_id={uuid}`

**Файл:** `apps/backend/app/api/v1/leaderboard.py` (расширение)

### Контракт (из TZ §3.7):
```json
{
  "habit_id": "uuid",
  "stat_name": "Эстетика тела",
  "metric_label": "Очки характеристики",
  "members": [
    {"rank": 1, "user_id": 123, "first_name_initial": "Д", "value": 87,
     "total_value": 142, "status_name": "Практик", "is_frozen": false}
  ]
}
```

### Критерий «готово»
- [ ] `ORDER BY value DESC, user_id ASC`
- [ ] Исключаются `membership.status = 'left'`
- [ ] `first_name_initial` = первая буква (ФЗ-152 минимум PII)

## Task 3.12: T6/T8 — whitelist в conftest

**Файлы:** `apps/backend/tests/conftest.py` + `apps/worker/tests/conftest.py`

### Что сделать

Добавить `UserStats` и `UserStatus` в whitelist моделей для `_remap_postgres_types_for_sqlite` (если используется). Иначе тесты Фазы B упадут с `TypeError: SQLite does not support type UUID/JSONB/INET`.

### Критерий «готово»
- [ ] `make test` (backend + worker) — все тесты Фазы B проходят
- [ ] T6/T8 закрыты (как требует TZ §8.1)

---

# Фаза 4 — Frontend (3-5 дней)

**Цель:** все 7 пользовательских страниц + Admin работают на реальном API. Плюс экран персонажа.

## Task 4.1: подключить `Marketplace` к API

**Файл:** `apps/frontend/src/pages/Marketplace/MarketplacePage.tsx`

### Что сделать
- Создать хук `useMarketplaceHabits()` в `shared/api/habits.ts` → `GET /api/v1/habits` (только `is_active=true AND archived_at IS NULL`)
- Заменить мок-данные на хук
- Loading / error / empty states

### Критерий «готово»
- [ ] На проде видны реальные клубы (если есть)
- [ ] Loading spinner + error toast
- [ ] Пустой state если нет клубов

## Task 4.2: подключить `Today`

**Файл:** `apps/frontend/src/pages/Today/TodayPage.tsx`

### Что сделать
- Хук `useToday(habitId)` → `GET /api/v1/habits/{id}/today`
- SSE через `useTodayStream` (уже есть с Фазы 0 prod-readiness)
- Кнопка «Сделать чек-ин» открывает bot (deep link)

### Критерий «готово»
- [ ] Видно реальный статус чек-ина за сегодня
- [ ] Real-time обновление через SSE (без polling)

## Task 4.3: подключить `Members`

**Файл:** `apps/frontend/src/pages/Members/`

### Что сделать
- Хук `useMembers(habitId)` → `GET /api/v1/habits/{id}/members`
- Хук `useCatch(habitId)` → mutation `POST /api/v1/members/{m_id}/catch`
- Кнопка «Поймать» с подтверждением

### Критерий «готово»
- [ ] Список участников с avatar + initials
- [ ] Кнопка «Поймать» → мгновенный UI feedback (optimistic update)

## Task 4.4: подключить `Balance` (wallet)

**Файл:** `apps/frontend/src/pages/Balance/` или вкладка в `Profile`

### Что сделать
- Хук `useWallet()` → `GET /api/v1/me/wallet` (уже есть)
- `TopUpModal` (уже есть) с пресетами 299/599/999/1999₽

### Критерий «готово»
- [ ] Видно текущий баланс
- [ ] Кнопка «Пополнить» работает (мок OK)

## Task 4.5: подключить `Leaderboard`

**Файл:** `apps/frontend/src/pages/Leaderboard/LeaderboardPage.tsx`

### Что сделать
- Хук `useLeaderboard(habitId)` → `GET /api/v1/leaderboard/streak` (существующий)
- Новый таб «📊 Характеристика» (после Фазы 3) → `useLeaderboardStat(habitId)`

### Критерий «готово»
- [ ] Табы: 🔥 Серии / 🎯 Ловцы / 💀 Позор
- [ ] После Фазы 3: добавить 📊 Характеристика

## Task 4.6: подключить `Profile`

**Файл:** `apps/frontend/src/pages/Profile/ProfilePage.tsx`

### Что сделать
- Хук `useMe()` → `GET /api/v1/users/me`
- Аватар через `/api/v1/users/{id}/photo`

### Критерий «готово»
- [ ] Фото профиля (с fallback на инициалы)
- [ ] Ссылка на «Мой персонаж» (после Фазы 3)

## Task 4.7: подключить `Onboarding` (join + pay)

**Файл:** `apps/frontend/src/pages/Onboarding/`

### Что сделать
- `JoinPayModal` (уже есть из Pravki-subscribe-and-join)
- `useJoinAndPay()` (уже есть)

### Критерий «готово»
- [ ] Чекбокс подписки + пресеты депозита
- [ ] POST `/api/v1/payments/subscribe` → success

## Task 4.8: экран «Мой персонаж» (после Фазы 3)

**Файл:** новый `apps/frontend/src/pages/Character/CharacterPage.tsx`

### Что сделать
- Хук `useCharacter()` → `GET /api/v1/character/me`
- Карточка статуса (иконка + название + прогресс-бар до следующего)
- Список характеристик карточками (замороженные с ❄️)
- Level-up toast при изменении статуса

### Критерий «готово»
- [ ] Видно total_value + текущий статус + прогресс
- [ ] Замороженные характеристики визуально отличаются

## Task 4.9: таб «📊 Характеристика» в LeaderboardPage (после Фазы 3)

### Что сделать
- Хук `useLeaderboardStat(habitId)` → `GET /api/v1/leaderboard/stat`
- Таб показывается только если в клубе есть `UserStats.value > 0`

## Task 4.10: `LevelUpToast` (после Фазы 3)

### Что сделать
- Сравнение `total_value` до и после запроса
- Если статус изменился → toast + haptic `impact('medium')`

## Task 4.11: баннер «Характеристика заморожена» в Mini App (после Фазы 3)

**Приоритет:** 🟠 High (иначе юзер не поймёт почему кнопка «Поймать» не активна или что делать).
**Время:** 2-3 часа.
**Зависимости:** Task 3.10 (`GET /character/me` уже возвращает `is_frozen`).

### Что сделать

**Файл:** `apps/frontend/src/pages/Character/CharacterPage.tsx` (или баннер в `Profile`)

```tsx
// Логика отображения баннера
function FrozenStatBanner({ stats }: { stats: UserStats[] }) {
  const frozen = stats.filter(s => s.is_frozen);
  if (frozen.length === 0) return null;
  return (
    <div className="bg-coral/10 border-l-4 border-coral p-4 rounded">
      <h3>❄️ {frozen.length} характеристик заморожено</h3>
      <p>
        30+ дней без чек-ина. Чтобы разморозить: продлите подписку,
        пополните депозит (если нужно) и сделайте чек-ин.
      </p>
      {frozen.map(s => (
        <div key={s.habit_id} className="mt-2">
          <strong>{s.habit_title}</strong> — заморожена
          {s.frozen_at && ` (с ${formatDate(s.frozen_at)})`}
          {s.frozen_reason_text && `: "${s.frozen_reason_text}"`}
        </div>
      ))}
      <div className="flex gap-2 mt-3">
        <button onClick={openSubscription}>Продлить подписку</button>
        <button onClick={openDeposit}>Пополнить депозит</button>
        <button onClick={openBotCheckin}>Сделать чек-ин</button>
      </div>
    </div>
  );
}
```

### Также показать на странице клуба (Today, Members)

```tsx
// Если текущий юзер в этом клубе is_frozen=true
// показать жёлтый warning: "Ты не отмечался 30+ дней. Сделай чек-ин!"
```

### Критерий «готово»
- [ ] Баннер виден когда есть `is_frozen=true` статы
- [ ] Список замороженных статов с `habit_title` и датой
- [ ] 3 кнопки (продлить / пополнить / чек-ин) — открывают нужный flow
- [ ] Баннер скрывается сразу после успешного чек-ина (SSE event)
- [ ] Стиль: коралл/тёплый фон (из docs/05-ui-ux.md палитры)

---

# Фаза 5 — Техдолг (1-2 дня)

**Цель:** закрыть долги из recon'а + admin-фича, чтобы новые фичи ложились на чистый фундамент.

## Task 5.1: TD-1 — вынести бизнес-логику из роута `list_available_chats` в `HabitService`

**Файл:** `apps/backend/app/api/admin/v1/habits.py` (роут) → `apps/backend/app/services/habit_service.py` (метод `list_available_chats_with_reconcile`)

### Что сделать
- Перенести 200 строк логики (reconciliation, миграция чатов, удаление из Redis) в сервис
- Роут оставить как тонкую обёртку (10-15 строк)

### Критерий «готово»
- [ ] Юнит-тесты на `HabitService.list_available_chats_with_reconcile`
- [ ] Роут не делает бизнес-логики

## Task 5.2: TD-2 — rate-limit на Bot API вызовы

**Файл:** `apps/backend/app/api/admin/v1/habits.py` (`_verify_chats_via_telegram`, `_get_bot_id`)

### Что сделать
- In-process token bucket / asyncio.Lock на `getChatMember`+`getChat`
- Кэш результата `getChatMember` на 5-10 секунд

### Критерий «готово»
- [ ] При 50 чатах в клубе и 5 запросах подряд — не превышает 30 req/sec к Bot API
- [ ] Кэш 5-10 сек уменьшает дублирующие вызовы

## Task 5.3: TD-3 — публичный API в `HabitService`

**Файл:** `apps/backend/app/services/habit_service.py`

### Что сделать
- Добавить методы `unbind_chat(habit_id)` и `get_chats_for_reconcile()` — заменить прямой доступ к `service._habit_repo.X` из роута

## Task 5.4: TD-4 — тесты для `_verify_chats_via_telegram` и `chat_member.py`

**Файлы:** новые `apps/backend/tests/test_chat_preview.py` + `apps/bot/tests/test_chat_member.py`

### Что сделать
- Покрыть: Telegram API 200/400/chat_not_found/migrated_to_chat_id/bot_kicked
- Покрыть: бот-хендлер `my_chat_member` для IS_NOT_MEMBER → IS_MEMBER и обратно

## Task 5.5: 9 устаревших production-комментариев

**Файлы:** `apps/backend/app/core/exceptions.py:127`, `apps/backend/app/repositories/checkin_repository.py:119,125`, `apps/backend/app/repositories/habit_repository.py:167`, `apps/backend/app/repositories/penalty_repository.py:76,116`, `apps/backend/app/schemas/__init__.py:167`, `apps/backend/app/services/checkin_service.py:107,188,192`, `apps/backend/app/services/penalty_service.py:114,169,174-179,237,309`, `apps/backend/app/api/v1/internal_bot.py:330,406`

### Что сделать
- Заменить комментарии на `⚠️ DEPRECATED 2026-08-18 (Pravki-manual-catch) — <что вместо>`
- Per `AGENTS.md §12` (точечные правки, не переписывание)

### Критерий «готово»
- [ ] `git grep "apply_window_expired\|WINDOW_CLOSED_NO_CATCH" apps/backend/app/services/` не находит комментариев про «активный путь»

## Task 5.6: вернуть `build:` для frontend (вместо volume-mount workaround)

**Файлы:** `infra/docker-compose.yml` + `infra/docker/Dockerfile.frontend`

### Что сделать
- Расследовать первопричину overlay-конфликта на `@tanstack/react-query`
- Вернуть `build:` в compose
- Убрать volume-mount workaround

### Критерий «готово»
- [ ] `docker compose build frontend --no-cache` проходит
- [ ] Bundle переживает recreate

---

# Фаза 6 — Deploy & Production (1-2 дня каждая)

**Цель:** soft-launch готов. Не блокирует Фазы 1-5 (можно деплоить в процессе).

## Task 6.1: бэкапы PostgreSQL

**Файл:** `infra/backup/backup_cron.sh` (готов) + cron

### Что сделать
- Выбрать S3 (Yandex Object Storage — 4000₽ гранта для новых, Contabo Auto-Backup ~10€/мес)
- Настроить `aws cli` (или `mc` для Yandex)
- `crontab -e`: `0 4 * * * /app/infra/backup/backup_cron.sh`

### Критерий «готово»
- [ ] Ежедневный backup в S3 с retention 7/4/12
- [ ] Тестовое восстановление прошло успешно

## Task 6.2: Sentry DSN

### Что сделать
- Завести Sentry-проект, скопировать DSN
- В `/app/infra/.env`: `SENTRY_DSN=...`
- `docker compose up -d backend worker bot`

### Критерий «готово»
- [ ] Тестовая ошибка в backend → видна в Sentry UI

## Task 6.3: перенос PostgreSQL в Selectel managed

### Что сделать
- Купить managed PostgreSQL в Selectel (~2000₽/мес)
- `pg_dump` → `pg_restore` в новую БД
- Сменить `DATABASE_URL` в `/app/infra/.env`
- Рестарт backend/worker/bot

### Критерий «готово»
- [ ] БД работает в Selectel, коннекты из контейнеров есть
- [ ] ФЗ-152 соблюдён (ПДн в РФ)

## Task 6.4: load testing (1000 users)

### Что сделать
- Установить `locust` или `k6`
- Сценарий: 1000 одновременных юзеров делают чек-ин
- Цель: p99 < 500ms, нет 5xx

---

# Фаза 7 — Growth (2-3 недели)

**Цель:** cold start. Без пользователей продукт мёртв.

## Task 7.1: партнёрский кабинет (MVP)

**Источник:** `1_kabinet_partnera_MVP.md`

### Что сделать
- Mini App `cabinet.prideclub.fun` (или `/cabinet` маршрут в основном Mini App)
- Трекинг рефералов: `GET /api/v1/partners/me/referrals`
- Статус выплат
- Базовая аналитика

### Критерий «готово»
- [ ] Партнёр видит своих рефералов + начисленные бонусы

## Task 7.2: реферальная программа (30% lifetime revenue share)

**Источник:** `1_kabinet_partnera_MVP.md`

### Что сделать
- Генерация реферальных ссылок (`https://t.me/PrideClubBot?start=ref_{partner_id}`)
- Бот фиксирует referral в `users.referred_by_partner_id`
- Биллинг: `transactions(type=REVENUE_SHARE, amount=30% от подписки)`
- Cron `partner_payouts` раз в месяц

### Критерий «готово»
- [ ] При вводе реферальной ссылки бот сохраняет партнёра
- [ ] Каждая подписка реферала → партнёру 30%
- [ ] Выплаты считаются корректно

## Task 7.3: первая волна через лидеров сообществ

**Источник:** `3_zapusk_cherez_liderov_soobshestv_checklist.md`

### Что сделать
- Найти 10-20 лидеров сообществ (по `2_poisk_partnerov_instagram.md`)
- Предложить бесплатный доступ основателям
- Метрики успеха: конверсия, % чек-инов, retention

### Критерий «готово»
- [ ] 10 клубов создано через лидеров
- [ ] Retention > 50% за месяц

---

# Глобальные инварианты (применимы ко всем задачам)

> Из `AGENTS.md` + `docs/04-code-standards.md` + `docs/06-data-model.md`:

1. **Деньги — `int` копейки** (`Penalty.amount`, `Transaction.amount`, `Habit.price_month`, `Habit.penalty_amount`, `UserStats.value` — отдельная ось, не деньги, но тоже `BIGINT`).
2. **`user_id`** — только из `request.state.telegram_user` (после initData-валидации). Никогда параметром.
3. **Сервис НЕ вызывает `session.commit()`** (исключение — admin endpoint `/admin/v1/habits`, помечено комментарием). DI через конструктор.
4. **Бизнес-логика НЕ в роутах** — только в `services/`. Роут = тонкая обёртка.
5. **`lock_for_update`** на user для всех денежных операций.
6. **PII не логируется** — только `user_id`/`admin_id` (числовые). НЕ `first_name`, `username`.
7. **Async I/O** — `aiohttp` для HTTP, `asyncpg` для БД, `asyncio.sleep`, `asyncio.to_thread` для CPU.
8. **UNIQUE-индексы** на `(membership_id, date, reason)` — идемпотентность.
9. **Domain exceptions** в `core/exceptions.py`, глобальный handler в `main.py`. Никаких `try/except Exception` в роутах.
10. **Константы в `core/constants.py`** — никаких магических чисел.
11. **Frontend через хуки** над `shared/api` — никакого `fetch`/`axios` в компонентах.
12. **TypeScript strict** — `any` только с обоснованием.
13. **Деплой через `docker compose build <service> --no-cache` (image-based!)** — `docs/10-deploy.md`.

---

# Что НЕ делать (из AGENTS.md)

- ❌ Коммитить секреты, пароли, `.env`
- ❌ Коммитить **приватные** SSH-ключи (`id_ed25519_*` без `.pub`, `*.pem`)
- ❌ Править `/app` на сервере напрямую
- ❌ Использовать `docker compose down` без ок
- ❌ Коммитить от `Dim41g / ivanov1331d@gmail.com` (только Vegass)
- ❌ Пушить в `origin/main` без явного "ок"
- ❌ Использовать `any` в TypeScript без обоснования
- ❌ Логировать PII (`first_name`, `username`)
- ❌ Делать "быстрых" изменений на сервере без плана в чате
- ❌ Переписывать документацию целиком
- ❌ Добавлять бизнес-логику в роуты
- ❌ Удалять что-либо в `/tmp` (включая бэкапы) без отдельного "ок"
- ❌ Делать правки по собственной инициативе в проде

---

# Definition of Done (для каждой задачи)

- [ ] Код соответствует `docs/04-code-standards.md` (layered architecture, DI, async, типизация)
- [ ] Юнит-тест + edge case покрыты
- [ ] `make test` зелёный (384+ для backend, 77+ для worker, 40+ для bot, 68+ для frontend)
- [ ] `make lint` чистый (ruff + mypy)
- [ ] `make migrate-test` проходит (если менялась схема)
- [ ] Нет `float`/`Decimal` для денег (грепнуть `rg "Decimal\\(|float\\("`)
- [ ] Middleware не обойден (auth через `request.state.telegram_user`)
- [ ] PII не в логах (грепнуть `rg "first_name|username" apps/ --type py`)
- [ ] Логи + метрики на критических операциях (`logger.info(..., extra={"duration_ms": ...})`)
- [ ] Если менялась документация — соответствующий `docs/*.md` обновлён **тем же коммитом**

---

# Карта задач (быстрый обзор)

| Фаза | Задач | Время | Блокирует прод? |
|---|---|---|---|
| 0 | 1 (Task 0.1) | 30 мин | нет (0₽), но стрельнёт на первом юзере |
| 1 | 3 (Tasks 1.1-1.3) | 1-2 дня | нет, но лидерборд мёртвый |
| 2 | 4 (Tasks 2.1-2.4) | 1 неделя | нет (сезонов нет) |
| 3 | 12 (Tasks 3.1-3.12) | 2-3 недели | нет, но это центральная ТЗ-фича |
| 4 | 11 (Tasks 4.1-4.11) | 3-5 дней | да — без UI продукт не работает |
| 5 | 6 (Tasks 5.1-5.6) | 1-2 дня | нет |
| 6 | 4 (Tasks 6.1-6.4) | по 1 дню | нет (для soft-launch) |
| 7 | 3 (Tasks 7.1-7.3) | 2-3 недели | нет, но без роста нет пользователей |
| **Всего** | **~44 задачи** | **5-6 недель** | |

---

# С чего начать СЕГОДНЯ (одна команда)

```bash
# 1. Подтянуть origin (нужен push 2 docs-коммитов)
git push origin feature/qa-batch-2026-08-14

# 2. Создать ветку для Task 0.1
git checkout -b fix/apply-catch-deposit-zero-waived

# 3. Правка penalty_service.py:171-177 (5 строк)

# 4. Тесты + commit + push
make test
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev commit -am "fix(penalty): apply_catch deposit=0 writes WAIVED marker (#17)"
git push origin fix/apply-catch-deposit-zero-waived

# 5. Deploy (по отдельному "ок" пользователя)
```

**Первая задача = Task 0.1** (единственная «бьёт деньги» дыра, 30 мин, 5 строк + 2 теста). После неё — Фаза 1 (bonus wiring), и т.д.

---

# Сценарий от и до — простым языком

> **Зачем эта секция:** показать логику продукта «с высоты», без технических терминов.
> Если ты поймёшь этот сценарий — поймёшь, ради чего весь проект.

## Кто участвует

| Роль | Кто это | Что делает |
|---|---|---|
| **Куратор** (админ) | Ты или твой партнёр | Создаёт клубы, настраивает окна чек-ина, размер штрафов |
| **Участник** | Обычный юзер в Telegram | Вступает в клуб, платит, делает чек-ины |
| **Ловец** | Тот же участник, но в роли охотника | Ловит прогульщиков, получает бонус |
| **Бот** | `@PrideClubBot` в Telegram | Принимает видео-кружки, проверяет правила, отвечает |
| **Backend** | Сервер с PostgreSQL/Redis | Считает деньги, штрафы, бонусы, лидерборды |
| **Mini App** | `app.prideclub.fun` (веб-интерфейс) | Показывает кошелёк, клуб, лидерборды |

---

## Общая идея (1 абзац)

Участник платит 1000₽ за месяц подписки + кладёт 750-1000₽ депозита в клуб. Каждый день в окне чек-ина он отправляет боту видео-кружок как доказательство, что выполнил привычку. Если не отметился — другой участник может «поймать» его и получить бонус, а с депозита прогульщика списывается штраф. Штрафы копятся в общем призовом фонде клуба. В конце сезона (30 дней) топ-5 участников по характеристикам получают призы из фонда (35/25/20/12/8%).

## Жизненный цикл клуба (30 дней)

```
День 0: КУРАТОР СОЗДАЁТ КЛУБ
    │
    ├─→ Заполняет в Admin Mini App:
    │   - Название ("Планка 30 мин")
    │   - Фото
    │   - Telegram-ссылка на чат клуба
    │   - Окно чек-ина (например, 09:00-21:00 по Москве)
    │   - Размер штрафа (250₽)
    │   - Стоимость входа (1000₽/мес)
    │   - Название характеристики ("Дисциплина")
    │   - Допустимые типы чек-ина (видео-кружок / фото / текст)
    │
    └─→ Клуб создан с is_active=false (не виден в каталоге)
        Куратор жмёт "Активировать" → клуб появляется в каталоге

День 1: УЧАСТНИК ВСТУПАЕТ
    │
    ├─→ Открывает Mini App → каталог клубов
    ├─→ Видит "Планка 30 мин" → жмёт "Вступить"
    ├─→ Выбирает пресет депозита (750 / 1000 / 1500₽)
    ├─→ Нажимает "Оплатить"
    │   - POST /api/v1/payments/subscribe
    │   - Один платёж: 1000₽ подписка + 750₽ депозит
    │   - В БД: users.deposit_balance += 750
    │   - В БД: membership.status = 'active'
    │   - В БД: transactions(2 записи: подписка + депозит)
    │
    └─→ Участник видит "Добро пожаловать в клуб!"

Дни 1-30: КАЖДЫЙ ДЕНЬ В ОКНЕ ЧЕК-ИНА
    │
    ├─→ В 09:00 (начало окна) бот присылает:
    │   "Доброе утро! Время чек-ина. Отправь видео-кружок."
    │
    ├─→ Участник снимает кружок → отправляет в бот
    │
    ├─→ Бот ПРОВЕРЯЕТ (за 100 мс, до отправки в backend):
    │   ✓ Окно открыто? (09:00-21:00)
    │   ✓ Не пересланное сообщение?
    │   ✓ Правильный тип (видео-кружок / фото)?
    │   ✓ Уже отмечался сегодня? (нет — пропускаем)
    │   ✓ Подписка активна?
    │   ✓ Не на паузе (deposit > 0)?
    │   ✓ Не был пойман сегодня?
    │
    ├─→ Всё ок → бот: "Принято! 💪"
    │   - Backend: INSERT checkin (или skip если уже есть)
    │   - Backend: UserStats.value += 2 (характеристика растёт)
    │   - Backend: SSE broadcast → Mini App обновляется у всех зрителей
    │
    └─→ Всё не ок → бот: "Не принято. Причина: <человеческим языком>"
        (например: "Окно чек-ина закрыто" / "Подписка истекла, продли в Mini App")

ЕСЛИ НЕ ОТМЕТИЛСЯ до 21:00 (конец окна чек-ина):
    │
    ├─→ Окно ловли открыто: 21:00 — 07:00 (10 часов: от конца окна чек-ина до
    │   начала следующего окна чек-ина минус `CATCH_WINDOW_BUFFER_HOURS=2`).
    │   В этом окне у всех остальных в Mini App кнопка «Поймать» активна.
    ├─→ Другой участник жмёт "Поймать @username"
    │   - POST /api/v1/members/{victim_id}/catch
    │   - Backend: apply_catch под user-lock
    │   - Списание 250₽ с депозита нарушителя
    │   - Запись Penalty(reason=CAUGHT, amount=250)
    │   - Бонус 50₽ (или +1 bonus_points) ловцу
    │   - +200₽ в Habit.prize_pool (призовой фонд)
    │   - SSE: "поймали @username" всем участникам
    │
    └─→ Если deposit < 250 → списание min(penalty, deposit), WAIVED-маркер
        (после topup не спишет повторно — Task 0.1)

ПОСЛЕ ЗАКРЫТИЯ ОКНА ЛОВЛИ (cron каждый час):
    │
    ├─→ Для каждого непойманного и не отметившегося:
    │   - Checkin(status='missed') — для истории/UI
    │   - recompute_pause_status(user_id) — sync статуса с депозитом
    │   - НЕ Penalty, НЕ Transaction (ручная поимка = единственный штраф)
    │
    └─→ Если deposit = 0 → membership.status = 'paused'
        (юзер не может делать чек-ины до topup)

День 30: КОНЕЦ СЕЗОНА (автоматически через cron close_season)
    │
    ├─→ Подсчёт итогового призового фонда клуба
    │   (например, 15 000₽ за 30 дней)
    │
    ├─→ Распределение топ-5:
    │   - 1 место (топ по характеристике) — 5 250₽ (35%)
    │   - 2 место — 3 750₽ (25%)
    │   - 3 место — 3 000₽ (20%)
    │   - 4 место — 1 800₽ (12%)
    │   - 5 место — 1 200₽ (8%)
    │   - Итого: 15 000₽ = 100% (без остатка)
    │
    └─→ Каждому победителю — Transaction(type=PRIZE)
        Выплата через Lava.top/Tribute (реквизиты карты)

ПОСЛЕ СЕЗОНА: УЧАСТНИК МОЖЕТ:
    ├─→ Остаться в клубе на новый сезон
    ├─→ Выйти из клуба (deposit остаётся на счёте, можно использовать в другом клубе)
    └─→ Завести новую привычку в новом клубе
```

---

## Геймификация (характеристики и статусы) — простым языком

Параллельно с деньгами у каждого участника в каждом клубе есть **характеристика** (условные очки, НЕ рубли). Это отдельная ось прогресса — мотивация «сверху» денег.

### Как растёт характеристика

```
+2 очка за каждый успешный чек-ин
-1 очко за каждый штраф (когда тебя поймали)
ХАРАКТЕРИСТИКА НИКОГДА НЕ УХОДИТ В МИНУС (floor на 0)

Пример за 30 дней:
  25 успешных чек-инов × 2 = +50
  2 поимки × (-1) = -2
  Итого: +48 очков за сезон
```

### Глобальный статус (по сумме ВСЕХ характеристик)

```
0-29 очков    → Новичок
30-149 очков  → Практик
150-499 очков → Мастер
500+ очков    → Легенда

Пороги — ИНДИВИДУАЛЬНЫЕ для каждой характеристики (настраивается в клубе).
```

### Заморозка характеристики

Если участник **30 дней не делает чек-ин** в каком-то клубе:
```
- Характеристика "замораживается" (is_frozen=true)
- Иконка ❄️ в лидерборде
- value сохраняется, но НЕ растёт и НЕ падает
- Любой следующий чек-ин АВТОМАТИЧЕСКИ размораживает
- Выход из клуба НЕ удаляет историю (при возврате восстанавливается)
```

**Важно:** заморозка характеристики ≠ пауза членства. Это два независимых механизма:
- Пауза (membership.status=paused) — депозит=0, чек-ины не принимаются
- Заморозка (UserStats.is_frozen) — после 30 дней без чек-ина **в ЭТОМ клубе**.
  Членство (membership.status) может оставаться ACTIVE, и юзер технически может
  ВОЗОБНОВИТЬ чек-ины (например, вспомнил про клуб) — при первом успешном чек-ине
  характеристика автоматически разморозится (`is_frozen=false`, `frozen_at=NULL`).

---

## Движение денег (финансовая схема)

```
Юзер платит
  ├─ 1000₽ → подписка (Transaction type=SUBSCRIPTION)
  └─ 750₽  → депозит (User.deposit_balance, Transaction type=DEPOSIT_TOPUP)
  
Юзер пропустил чек-ин → пойман другим
  ├─ 250₽ → списание с депозита (User.deposit_balance -= 250)
  ├─ 50₽  → бонус ловцу (User.bonus_points += 1 → после фикса #1)
  └─ 200₽ → призовой фонд клуба (Habit.prize_pool += 200)
  
Конец сезона (15 000₽ в фонде)
  ├─ 35% → 1 место (5 250₽)
  ├─ 25% → 2 место (3 750₽)
  ├─ 20% → 3 место (3 000₽)
  ├─ 12% → 4 место (1 800₽)
  └─ 8%  → 5 место (1 200₽)
  
Выплата: Lava.top / Tribute → карта победителя
  (При сумме ≤ 4000₽/год на человека — налогов нет,
   паспортные данные не нужны)
```

---

## Что видит каждый участник

### Mini App "Сегодня" (главный экран)
```
┌─────────────────────────────────┐
│  Планка 30 мин                  │
│  Окно чек-ина: 09:00-21:00     │
│  ────────────────────────────  │
│  ✓ Сегодня отметился (10:42)   │
│  🔥 Серия: 12 дней подряд      │
│  ────────────────────────────  │
│  [Открыть чат клуба]            │
│  [Открыть бота для чек-ина]    │
└─────────────────────────────────┘
```

### Mini App "Кошелёк"
```
┌─────────────────────────────────┐
│  Депозит: 250₽ (было 750₽)     │
│  ────────────────────────────  │
│  Списано за месяц: 500₽        │
│  (2 поимки × 250₽)             │
│  ────────────────────────────  │
│  [Пополнить 750 / 1000 / 1500₽]│
└─────────────────────────────────┘
```

### Mini App "Участники клуба"
```
┌─────────────────────────────────┐
│  Можно поймать сегодня:         │
│  ├─ @vasya (окно 09:00-21:00)  │
│  │   [Поймать]                  │
│  └─ @petya (окно 09:00-21:00)  │
│      [Поймать]                  │
│  ────────────────────────────  │
│  Уже отметились: 5 чел.         │
│  Статус: paused — 1 чел.        │
└─────────────────────────────────┘
```

### Mini App "Мой персонаж" (после Фазы 3)
```
┌─────────────────────────────────┐
│  💪 Дисциплина: 87 очков        │
│  🧠 Интеллект: 24 очка          │
│  🏃 Активность: 142 очка         │
│  ────────────────────────────  │
│  Сумма: 253 → Практик           │
│  До "Мастер" (150+): 297 очков  │
│  ▓▓▓▓▓▓▓▓░░ 50%                │
│  ────────────────────────────  │
│  🧊 Чтение — заморожено          │
│  "Отказался расти дальше"        │
└─────────────────────────────────┘
```

### Бот (Telegram chat)
```
09:00  "Доброе утро! Время чек-ина."
10:42  Участник: [видео-кружок 15 сек]
10:42  Бот: "✅ Принято, +2 💪"
12:30  Другой: [кружок 8 сек]
12:30  Бот: "✅ Принято!"
21:05  "Окно чек-ина закрыто."
22:30  Другой: "лови @vasya"
22:30  Бот: "🎯 Поймал @vasya. +1 бонус."
```

### Лидерборд (после Фазы 3)
```
┌─────────────────────────────────┐
│ 🔥 Серии    🎯 Ловцы    💀 Позор
│ 📊 Характеристика              │
│  1. @vasya    Дисциплина  142  │
│  2. @masha   Дисциплина  138  │
│  3. @petya   Дисциплина  87   │
│  ...                            │
└─────────────────────────────────┘
```

---

## Edge-cases (как продукт ведёт себя в сложных ситуациях)

| Ситуация | Что происходит | Статус |
|---|---|---|
| Участник вступил в клуб вчера, депозит=0, сегодня пропустил | На паузе, чек-ин не принимается | ✅ Task 0.1 + Z-19 |
| Участник пойман, deposit < штрафа | Списывается min(штраф, депозит), WAIVED-маркер | ✅ Task 0.1 |
| Участник вступил в клуб после начала сезона | Штрафы начинают считаться со дня вступления, season stats отдельно | ✅ TZ §3.6 |
| Участник не делает чек-ин 30 дней | Характеристика замораживается, membership остаётся ACTIVE | ❌ Task 3.9 (не сделано) |
| Участник возвращается из заморозки (подписка истекла) | Шаг 1: `subscription_expired` блокирует → "продли подписку". Шаг 2 (если deposit<penalty): `membership_paused` → "пополни депозит". Шаг 3: успешный чек-ин → `is_frozen=false` автоматически | 🟡 частично: блокировки (✅ после Pravki-subscription-2026-08-17), автоматический сброс (❌ Task 3.6), UX-баннер (❌ Task 4.11) |
| Призовой фонд пуст (никто не нарушил) | Rollover в след. сезон / бонусы / доплата организатора | ❌ Task 2.3 (распределение) |
| Админ удаляет клуб с участниками | Soft-delete (архив), участники сохраняют историю | ✅ c7f8d87 |
| Бот молчит (webhook down) | Чек-ины не доставляются, но юзер может сделать через Mini App | 🟡 edge |
| Два юзера одновременно ловят одну жертву | UNIQUE на (membership, date, reason) — только один catch срабатывает | ✅ |
| Участник 2 раза отправил один кружок | Бот: "уже отметился сегодня" (UNIQUE на checkin_id) | ✅ |

---

## Состояние продукта СЕЙЧАС (2026-08-19) vs ЦЕЛЕВОЕ

| Что | Сейчас | Целевое (после Фаз 0-5) |
|---|---|---|
| Чек-ины (с видео-кружком) | ✅ Работает | ✅ |
| Бот pre-filter (5 round fix) | ✅ Работает | ✅ |
| SSE real-time обновления | ✅ Работает | ✅ |
| Депозит + штрафы | ✅ Работает | ✅ |
| Подписка (subscribe_and_join) | ✅ Работает | ✅ |
| Manual catch (Z-1/Z-2/Z-3) | ✅ Задеплоен | ✅ |
| WAIVED-маркер для deposit=0 | 🟡 Частично (Task 0.1) | ✅ |
| Bonus начисление ловцу | ❌ Не вызывается | ✅ (Task 1.1-1.3) |
| Лидерборд "Охотники" | ❌ Пустой | ✅ (Task 1.x) |
| Призовой фонд в конце сезона | ❌ Раздаёт 0₽ | ✅ (Task 2.1-2.4) |
| Характеристики (Фаза B) | ❌ Нет таблиц | ✅ (Task 3.1-3.12) |
| Заморозка характеристики | ❌ Нет | ✅ (Task 3.9) |
| Глобальный статус (Новичок → Практик → ...) | ❌ Нет | ✅ (Task 3.10) |
| Frontend страницы к API | ❌ Моки | ✅ (Task 4.1-4.7) |
| Экран "Мой персонаж" | ❌ Нет | ✅ (Task 4.8) |
| Лидерборд по характеристике | ❌ Нет | ✅ (Task 4.9) |
| LevelUpToast | ❌ Нет | ✅ (Task 4.10) |
| Бэкапы PostgreSQL | ❌ Не развёрнуты | 🟡 (Task 6.1) |
| Sentry DSN | ❌ no-op | 🟡 (Task 6.2) |
| Партнёрский кабинет | ❌ Только описание | 🟡 (Task 7.1) |

**После Фаз 0-5 (5-6 недель) — полностью рабочий MVP.**

---

## Метафора (чтобы запомнить)

> **Представь: спортзал с общим пулом денег за дисциплину.**
>
> - Каждое утро ты приходишь и снимаешь видео "я сегодня потренировался"
> - Если не пришёл — другие участники могут "поймать" тебя, забрать 250₽ твоего депозита (50₽ им как бонус, 200₽ в общий котёл)
> - Через месяц топ-5 самых дисциплинированных делят котёл
> - Параллельно ведётся счёт "твоей формы" (характеристика), который даёт бейджи и статус
> - Если ты 30 дней не появляешься — "форма" замораживается, но не обнуляется
> - Бросил клуб? Деньги остались на твоём счёте, вернись когда угодно

**Это весь продукт. Остальное — технические детали, чтобы это работало.**

---

**Дата создания:** 2026-08-19
**Следующий review:** после выполнения Task 0.1 + Фазы 1 (~2 дня)
**Версия:** 1.0 — execution plan + сценарий от и до простым языком.
