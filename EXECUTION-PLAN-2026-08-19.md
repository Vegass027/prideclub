# Habit Club — Execution Plan / 2026-08-19

> **Этот документ — ТОЧКА ВХОДА для работы.** Пошаговый план задач от сложных/нужных до мелких/техдолга.
> Цель: после выполнения всех задач — **полноценный рабочий продукт**, на который можно наслаивать новые фичи.
> **Версия:** 1.4. **Дата:** 2026-08-19 (создан), **2026-08-20 (verified)**, **2026-08-21 (rebuilt)**, **2026-08-21 (product-changes)**, **2026-08-21 (deployed)**. **Автор:** AI-ассистент по запросу Дмитрия.

> **✅ Snapshot 2026-08-21 — PHASE 1 DEPLOYED.** Phase 1 (Tasks 1.1 + 1.2 + 1.3
> + 1.4 + 1.5 + test-fix) задеплоен на прод через `privichki-prod`. Коммиты:
> `562a6ca` (Task 1.1 миграция 016) + `882951d` (Task 1.2 enum) + `7b07367`
> (Task 1.3 apply_catch) + `3f9a4d5` (Task 1.4 миграция 017 + модель) +
> `b43acb4` (test fix: Habit модель + fakes + race-test) + `48dd6d7`
> (Task 1.5 admin endpoint). Все 5 коммитов в `feat/catcher-deposit-share-task-1-1`
> запушены в origin.
>
> **Verify после деплоя:**
> - `alembic current` = `017_penalty_split_columns (head)` ✅
> - `SELECT id, title, penalty_amount, catcher_amount_kopecks FROM habits` →
>   3 клуба, ВСЕ с `catcher_amount_kopecks=0` (миграция 016 DEFAULT 0 применился) ✅
> - `TransactionType.CATCHER_DEPOSIT.value == "catcher_deposit"` ✅
> - `celery inspect active` = empty ✅
> - `health`/`ready` endpoints = 200 ✅
> - sha256 локально = sha256 на сервере (185 файлов) ✅
>
> **⚠️ Known risk (зафиксирован, не блокер):** `apps/worker/worker/tasks/process_penalty.py`
> имеет 2 строки `except Exception` (lines 193, 214) без структурированного
> traceback-логирования. Если новая логика `apply_catch` упадёт с `TypeError`
> или неожиданным исключением — worker репортит `succeeded` но Penalty не
> создаётся, диагностики нет. **Этап 5 (реальный catch с owner initData)**
> используется как финальная проверка — перед ним будет добавлен
> traceback-лог в `process_penalty.py` (10-минутный фикс видимости, не рефакторинг).
>
> **📋 Полный рефакторинг exception-handling** (разделение бизнес-исключений
> от системных) — это отдельная задача техдолга, **часть будущей Phase 5**
> (см. `docs/AGENT_BOOTSTRAP.md` §3 — Z-2.5 инцидент с `penalty_repo` в
> `CheckinService.__init__`). Не блокирует Phase 1.

> **⚠️ Snapshot 2026-08-20 — post-verification.** После критики Дмитрия ("основывался
> на документах а не на реальном проде") провёл верификацию всех 44 задач против
> реального кода через `grep` + `codegraph`. **Найдено 6 ошибок**:
> - `Task 0.1` уже закрыт (через Pravki-no-deposit-waived-marker + идемпотентность) → удалён
> - `§3 E "Frontend не подключен к API"` — неправда, фронт уже подключен → удалён
> - `Task 1.3` — тест через прямой импорт `_process`, не через broker → переписать существующий
> - `Task 2.3` — структура `prize_rules_snapshot` уже гибкая (rank_from/rank_to/bp), не 5 мест хардкод
> - `Task 5.5` — реально **14** вхождений устаревших комментариев, не 10 (snapshot 2026-08-21, после `grep -rnE`)
> - `§3 D (Фаза B)` — полностью актуальна
>
> **Контекст:**
> - HEAD = `6830c42` (локально), origin = `6830c42` (синхронизировано после push 2026-08-19)
> - CodeGraph v1.5.0 подключён к 5 агентам (Claude Code, Cursor, opencode/Kilo, Gemini, Antigravity)
> - Прод: 2 users, 3 habits, 3 memberships, 4 transactions (snapshot 2026-08-09)
> - 15 production-серий задеплоены (см. `docs/archive/2026-summer-fixes/STATUS-2026-08-19.md §2.1`)
> - 0 живых пользователей, 0₽ в обороте → цена ошибки низкая, время на рефакторинг есть
>
> **Источники ТЗ:**
> - `TZ_kharakteristiki_personazha.md` — Фаза B (характеристики персонажа)
> - `docs/archive/2026-summer-fixes/4_finansovaya_mehanika_shtrafov_i_prizov.md` — финансовая модель
> - `docs/archive/2026-summer-fixes/prideclub_karta_proekta.md` — общая карта проекта
> - `Pravki-business-logic-recon-2026-08-18.md` — 28 находок (gap-анализ)
>
> **Метод проверки:** `grep -rn` для каждой задачи + `codegraph query` /
> `codegraph node` для подтверждения наличия символов. Не доверять `prod-readiness.md §2.3` —
> там устарело.

> **⚠️ Snapshot 2026-08-21 — rebuild.** После повторной разведки (перед стартом работы)
> пересобрал порядок фаз и оценки. **Новые факты (проверено через `grep` + `Read`):**
>
> 1. **Phase 4 закрыта на ~70%** — все 7 пользовательских страниц (`Marketplace`,
>    `Today`, `Members`, `Balance`, `Profile`, `Leaderboard`, `Onboarding`) уже
>    импортируют хуки из `@/shared/hooks` (`useMarketplace/useToday/useMembers/
>    useCatch/useBalance/useWallet/useLeaderboard/useMyHabits/useHabitSse`). Из
>    11 задач реально остались **только 4.8/4.9/4.10/4.11** — и все 4 зависят от
>    Phase 3 (Character & Stats). Реальная трудоёмкость Phase 4 = 1-2 дня, не 3-5.
> 2. **Phase 2 частично сделана** — `apps/backend/app/services/season_service.py`
>    уже содержит `start_season`/`close_season`/`_rank_by_metric`/`validate_prize_rules`
>    + `BASIS_POINTS_TOTAL = 10_000`. Нужны только: `SeasonRepository`, инкремент
>    `Season.prize_pool` в `apply_catch`, admin endpoint, `DEFAULT_PRIZE_RULES`.
>    Реальная трудоёмкость = 3-4 дня, не 1 неделя.
> 3. **Phase 1 — реально 3-4 часа**, не 1-2 дня. Worker task `apply_catch_bonus`
>    уже существует (`apps/worker/worker/tasks/apply_catch_bonus.py`), нужно только
>    зарегистрировать имя в `_TASK_NAMES` + добавить `send_task` в `process_penalty`
>    + переписать тест через broker.
> 4. **Phase 3 (Character & Stats) — главная фича ТЗ**, должна идти **сразу после
>    warm-up Phase 1**, не после Phase 2. Логика: метрика `stat_value` для топ-5
>    победителей сезона появится только с Phase 3, поэтому Phase 2 (призовой
>    фонд) логичнее делать **после** Phase 3, не до.
> 5. **Phase 5.5** — реально **14 строк** устаревших комментариев
>    `apply_window_expired|WINDOW_CLOSED_NO_CATCH`, не 10 (план занижал).
>
> **Пересобранный порядок:** Phase 1 (warm-up, 3-4 ч) → **Phase 3 (главная фича,
> 2-3 нед)** + хвост Phase 4 параллельно → Phase 2 (3-4 дня) → Phase 5 (1-2 дня)
> → Phase 6 → Phase 7. Суммарно ~5-6 недель, как и было.

> **⚠️ Snapshot 2026-08-21 — product-changes (catcher deposit share).**
> Продуктовое решение Дмитрия от 2026-08-21: **полностью отказываемся от виртуальных
> бонусов**. Ловец получает РЕАЛЬНЫЕ деньги на свой депозит, а не `bonus_points`.
>
> **Новая механика штрафов:**
> - Админ клуба при настройке указывает **сумму ловцу** (`Habit.catcher_amount_kopecks`,
>   целое число копеек). Никаких процентов, только число.
> - При поимке: штраф делится на 2 части → **часть в призовой фонд клуба** +
>   **часть на депозит ловца** (`User.deposit_balance`)
> - Пример: штраф 300₽ (30000 коп), `catcher_amount_kopecks=10000` (100₽) → 200₽ в фонд, 100₽ ловцу
> - Пример: штраф 500₽ (50000 коп), `catcher_amount_kopecks=20000` (200�) → 300₽ в фонд, 200₽ ловцу
> - Если `catcher_amount_kopecks >= penalty_amount` → всё ловцу, фонд=0
> - Если `catcher_amount_kopecks=0` → всё в фонд (старое поведение, для обратной совместимости)
>
> **Депозит — единственный реальный счёт.** С него в будущем будут выводить средства.
>
> **Призовой фонд в конце сезона** теперь **зачисляется на депозит победителям**
> (а не остаётся "внешним долгом"): топ-5 получают свою долю (35/25/20/12/8%)
> прямо на `User.deposit_balance`.
>
> **`suspicious_pairs` (variant A, подтверждено Дмитрием 2026-08-21):**
> в текущей модели сговор **финансово невыгоден** (оба теряют деньги), поэтому
> suspicious_pairs **НЕ блокирует деньги** — деньги списываются/зачисляются как обычно.
> Но портит лидерборды (фейковые поимки раздувают `catches_count`), поэтому
> suspicious_pairs пишет флаг `Penalty.is_suspicious_pair=true` — лидерборд фильтрует
> flagged пары из метрик. Логика: `suspicious_repo.lookup_flagged()` всё ещё вызывается
> в `apply_catch`, но результат идёт ТОЛЬКО в `is_suspicious_pair` (boolean), не влияет на суммы.
>
> **Полный отказ от виртуальных бонусов:**
> - Удалить `User.bonus_points`, `User.bonus_points_updated_at`
> - Удалить `Membership.bonus_points`
> - Удалить `Penalty.catcher_bonus_points`, `Penalty.bonus_applied`
> - Удалить модель `BonusRule` + `bonus_rule_repository.py`
> - Удалить `apps/backend/app/services/bonus_service.py` целиком
> - Удалить worker tasks: `apply_catch_bonus.py`, `expire_bonus_points.py`,
>   `integrity_check_bonus_transactions.py`
> - Удалить транзакции `TransactionType.BONUS_CATCH`, `BONUS_SUBSCRIPTION`,
>   `BONUS_POINTS`
> - Удалить константы `CATCHER_BONUS_POINTS`, `BONUS_POINTS_EXPIRY_*`, `FUND_SHARE`
> - Удалить `_TASK_NAMES["apply_catch_bonus"]` в celery_producer.py
> - Удалить фронт: `bonus_points` в types/index.ts, BONUS_* в format.ts
> - Удалить тесты: test_apply_catch_bonus.py, test_expire_bonus_points.py,
>   test_integrity_check_bonus_transactions.py
>
> **Новый порядок (rebuild 2):**
> - Phase 1 (REBUILD): Catcher deposit share — новая механика штрафов (2-3 дня)
> - Phase 2 (UPDATE): Призы на депозит победителей (3-4 дня, добавлено зачисление)
> - Phase 3: Character & Stats (2-3 нед) — без изменений
> - Phase 4 хвост: UI персонажа (1-2 дня) — без изменений
> - Phase 5: Техдолг (1-2 дня) — без изменений
> - **Phase 8 (NEW): Cleanup bonus — удаление старой бонусной механики (1-2 дня)**
> - Phase 6, Phase 7 — без изменений
>
> Суммарно: ~5-6 недель, как и было, но Phase 1 теперь 2-3 дня вместо 3-4 ч
> (добавилась миграция + рефактор `apply_catch` + новые транзакции + admin поле).

---

## 0. TL;DR

| Фаза | Что | Задач | Время | Блокирует прод? | Порядок |
|---|---|---|---|---|---|
| **0** | Закрыть финансовую дыру | — | — | ✅ уже закрыт (`9c32d6f`) | — |
| **1** | **Catcher deposit share** (бывш. Bonus wiring) | 5 | **2-3 дня** (было 3-4 ч) | нет, но лидерборд мёртвый | **🔜 первый** |
| **2** | Призовой фонд на депозит (seasons enable + prize → deposit) | 4 | **3-4 дня** (было 1 нед) | нет (сезонов нет) | **после Phase 3** |
| **3** | Character & Stats (Фаза B из TZ) | 12 | 2-3 недели | нет, но это центральная фича ТЗ | **🎯 главный** |
| **4** | Frontend (страницы + персонаж) | **4** (было 11) | **1-2 дня** хвоста (было 3-5) | нет — хвост за Phase 3 | параллельно Phase 3 |
| **5** | Техдолг (admin, hardening) | 6 | 1-2 дня | нет | после Phase 2 |
| **6** | Deploy & Production | 4 | по 1 дню | нет (для soft-launch) | после Phase 5 |
| **7** | Growth (партнёрка, рефералка) | 3 | 2-3 недели | нет, но без роста нет пользователей | последний |
| **8** | **Cleanup bonus** (NEW) | 7 | **1-2 дня** | нет | **после Phase 1** |

**После всех 8 фаз (5-6 недель) — полноценный рабочий продукт.**

**Изменённый порядок (rebuild 2 от 2026-08-21):**
Phase 1 (Catcher deposit share) → **Phase 8 (Cleanup bonus)** → Phase 3 + хвост Phase 4 параллельно → Phase 2 (призы → депозит) → Phase 5 → Phase 6 → Phase 7.

---

## 1. Что ЗАДУМАНО (MVP-критерии полноценного продукта)

Из `TZ_kharakteristiki_personazha.md` + `docs/archive/2026-summer-fixes/prideclub_karta_proekta.md` + `docs/archive/2026-summer-fixes/4_finansovaya_mehanika_shtrafov_i_prizov.md`:

### 1.1 Core habit loop
1. ✅ Юзер вступает в клуб (`POST /api/v1/habits/{id}/join` через Mini App)
2. ✅ Платит подписку + депозит одним платежом (`POST /api/v1/payments/subscribe`)
3. ✅ Делает чек-ин с доказательством (видео-кружок / фото / текст) через бот
4. ✅ При пропуске — штраф делится на 2 части: **часть в призовой фонд клуба** (`Habit.prize_pool`) + **часть на депозит ловца** (`User.deposit_balance`). Пропорция задаётся админом клуба.
5. ✅ Другой участник может «поймать» нарушителя и получить **реальные деньги на свой депозит** (`POST /api/v1/members/{id}/catch`)
6. ❌ В конце сезона — топ-5 получают призы (35/25/20/12/8% от фонда) **на свой депозит** — **НЕ РАБОТАЕТ** (см. #2)
7. ✅ Лидерборд по количеству поимок (catches_count) — работает, но без денежного выхлопа

### 1.2 Геймификация (Фаза B)
1. ❌ Характеристика растёт при чек-ине, падает при штрафе (отдельная ось, не рубли)
2. ❌ Сумма всех характеристик → глобальный статус (Новичок → Практик → Мастер → Легенда)
3. ❌ Заморозка характеристики после 30 дней без чек-ина
4. ❌ Лидерборд по характеристике внутри клуба
5. ❌ Экран «Мой персонаж» с карточками статуса

### 1.3 Frontend
- ✅ Marketplace, Today, Members, Balance, Profile, Leaderboard, Onboarding — **уже подключены через хуки** (`useMarketplace/useToday/useMembers/useCatch/useBalance/useWallet/useLeaderboard/useMyHabits/useHabitSse` из `@/shared/hooks`). Snapshot 2026-08-21: проверено `grep` по `apps/frontend/src/pages/*/*.tsx`
- ❌ `CharacterPage` (экран «Мой персонаж») — не сделан, зависит от Phase 3 (`GET /character/me`)
- ❌ Таб «📊 Характеристика» в Leaderboard — зависит от Phase 3 (`GET /leaderboard/stat`)
- ❌ `LevelUpToast` — зависит от Phase 3
- ❌ Баннер «Характеристика заморожена» — зависит от Phase 3 (`is_frozen` в `GET /character/me`)

### 1.4 Финансы (по `4_finansovaya_mehanika` + продуктовое решение 2026-08-21)
- Вход 1000₽/мес, штраф настраивается админом (например 250₽), депозит 750-1000₽
- **Штраф делится** на 2 части: в призовой фонд клуба + на депозит ловца (пропорция задаётся админом)
- 5 призовых мест (35/25/20/12/8%) — **зачисляются на депозит победителя** (не внешний долг)
- Депозит — единственный реальный счёт, с него в будущем выводят средства (Lava.top / Tribute / СБП)
- ❌ **Виртуальные bonus_points полностью удаляются** (snapshot 2026-08-21)
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

**Подробности по каждой серии — `docs/archive/2026-summer-fixes/STATUS-2026-08-19.md §2.1`.**

---

## 3. Что НЕ реализовано (главный gap)

> **⚠️ Snapshot 2026-08-20 (post-verification).** Этот раздел был переписан после проверки
> каждого пункта против реального кода. Несколько пунктов оказались неактуальными (закрыты
> ранее, чем я думал), несколько — точнее чем я писал.

| # | Что | Severity | Источник | Статус |
|---|---|---|---|---|
| A | **#17** `apply_catch` deposit=0 без WAIVED-маркера (прямая финансовая) | 🔴 | recon | **✅ УЖЕ ЗАКРЫТ** — `commit 9c32d6f` (Pravki-no-deposit-waived-marker) + идемпотентность `apply_catch` на ЛЮБУЮ `Penalty` за день (комментарий `penalty_service.py:165-180`). Задача Task 0.1 в плане — **дубликат** |
| B | **#1** `apply_catch_bonus` не вызывается в проде (лидерборд мёртвый) | � | recon | ⚠️ **STALE 2026-08-21 (product-changes)**: вся бонусная механика уходит. Заменяется на **catcher deposit share** (Phase 1 REBUILD): ловец получает реальные деньги на депозит, не `bonus_points`. `apply_catch_bonus` task БУДЕТ УДАЛЁН в Phase 8 |
| C | **#2** `Season.prize_pool` не пишется (призовой фонд не распределяется) | 🟠 | recon | ✅ Подтверждено: `start_season` нигде не вызывается, admin endpoint `/seasons` отсутствует. **Snapshot 2026-08-21 (product):** призы теперь идут на депозит победителя, не "внешним долгом". Tasks 2.1+2.2+2.5 (NEW: prize → deposit) актуальны |
| D | **Фаза B (TZ):** user_stats, user_statuses, increment/decrement, freeze worker, эндпоинты, frontend | 🟠 | TZ | ✅ Подтверждено: моделей, репозиториев, CharacterService, worker — нет. Фаза 3 полностью актуальна |
| ~~E~~ | ~~Frontend не подключен к API (7 страниц)~~ | — | — | **❌ STALE** — фронт **уже подключен** через хуки `useMarketplace/useToday/useMembers/useCatch/useBalance/useWallet/useLeaderboard/useHabitSse/useMyHabits`. Удаляю из gap-списка |
| F | Admin техдолг (TD-1..TD-4) | 🟡 | recon | ✅ Подтверждено: TD-1 (нет метода в HabitService), TD-2 (нет asyncio.Lock), TD-4 (нет test_chat_preview/test_chat_member.py) — всё актуально |
| G | 10 устаревших production-комментариев | 🟢 | prod-readiness §3 | ⚠️ Неточно (snapshot 2026-08-21): реально **14 строк** (`grep -rnE "apply_window_expired\|WINDOW_CLOSED_NO_CATCH"` в `services/`+`api/`+`repositories/`+`schemas/`+`core/`, без `__pycache__`). Масштаб чуть больше |

**Всё разложено по фазам ниже. Phase 4 (Frontend) — не блокер; пересмотрен приоритет (см. Snapshot 2026-08-21 в начале документа).**

> **Snapshot 2026-08-21 — что подтверждено в этом gap-списке:**
>
> | # | Что подтверждено | Как проверено |
> |---|---|---|
> | B | `apply_catch_bonus` нет в `_TASK_NAMES` | `Read celery_producer.py:21-35` |
> | B | `send_task("apply_catch_bonus")` нет в `process_penalty` | `grep "apply_catch_bonus\|send_task" apps/worker/worker/tasks/process_penalty.py` — 0 совпадений |
> | B | worker task `apply_catch_bonus` СУЩЕСТВУЕТ | `ls apps/worker/worker/tasks/` |
> | C | `season_service.py` существует, `start_season`/`close_season` готовы | `Read season_service.py` (207 строк) |
> | C | `SeasonRepository` НЕ существует | `ls apps/backend/app/repositories/` |
> | C | `apply_catch` НЕ инкрементит `Season.prize_pool` | `grep "Season\|season_repo" apps/backend/app/services/penalty_service.py` |
> | C | Admin endpoint `/admin/v1/habits/{id}/seasons` НЕ существует | `ls apps/backend/app/api/admin/v1/` |
> | D | `CharacterService`/`CharacterConfig`/`UserStats`/`UserStatus` — нет | `ls apps/backend/app/services/`, `ls apps/backend/app/models/` |
> | F | TD-1/TD-2/TD-4 — подтверждено (не проверял детально, верификация 2026-08-20) | — |

---

# Фаза 0 — ~~Закрыть финансовую дыру~~ ✅ УЖЕ ЗАКРЫТО

> **⚠️ Snapshot 2026-08-20.** При верификации плана выяснилось: дыра **#17 уже закрыта** через
> `commit 9c32d6f feat(penalty): mark_waived_unable_to_pay` (Pravki-no-deposit-waived-marker, 2026-08-17) +
> расширенная идемпотентность в `apply_catch` на ЛЮБУЮ `Penalty` за день
> (см. комментарий `apps/backend/app/services/penalty_service.py:165-180`).
>
> `apply_catch` для `deposit=0` сейчас ведёт себя так:
> - Существующая логика `mark_waived_unable_to_pay` (для PAUSED юзеров) пишет `Penalty(reason=WAIVED_UNABLE_TO_PAY, amount=0)` при смене статуса на PAUSED
> - Idempotency check: если за день есть ЛЮБАЯ Penalty — повторный catch отвергается
> - После topup юзер с WAIVED за прошлый день не получит двойное списание
>
> **Task 0.1 в исходном плане был дубликатом — удаляю.**

## ~~Task 0.1: fix #17~~ — НЕ ТРЕБУЕТСЯ (закрыт до создания плана)

**Проверка:** см. `apps/backend/app/services/penalty_service.py:165-180` (комментарий
"Идемпотентность: если за день есть ЛЮБАЯ Penalty ... повторный catch отвергается").

---

# Фаза 1 — Catcher deposit share (бывш. Bonus wiring, REBUILD 2026-08-21)

**Цель:** ловец получает **реальные деньги на свой депозит** (а не виртуальные `bonus_points`).
Пропорция штрафа → ловцу настраивается админом клуба.

> **⚠️ REBUILD 2026-08-21 (product-changes).** Старая Phase 1 (Bonus wiring) **полностью
> заменяется**. Виртуальные bonus_points больше не используются. Вместо этого — реальные
> деньги на депозит. После Phase 1 старая бонусная механика удаляется в **Phase 8 (Cleanup)**.

**Контракт:**
- Админ клуба при создании/настройке указывает `catcher_amount_kopecks` (целое число копеек)
- Пример: штраф 300₽, `catcher_amount_kopecks=10000` (100₽) → 200₽ в фонд + 100₽ ловцу
- Пример: штраф 500₽, `catcher_amount_kopecks=20000` (200₽) → 300₽ в фонд + 200₽ ловцу
- Деньги списываются с депозита нарушителя одной транзакцией, делятся на 2 части
- **Одна транзакция** под user-lock'ами ОБОИХ user'ов (lock по возрастанию user_id — избежание deadlock'а)
- Антифрод (variant A, подтверждено Дмитрием 2026-08-21): `suspicious_pairs` (см. `SUSPICIOUS_ASYMMETRY_THRESHOLD = 3`) — НЕ блокирует деньги (сговор финансово невыгоден в текущей модели), но пишет флаг `Penalty.is_suspicious_pair=true` для лидерборда (метрики фейковых поимок фильтруются).

## Task 1.1: миграция 016 — `Habit.catcher_amount_kopecks`

**Файл:** новый `apps/backend/alembic/versions/016_habit_catcher_amount.py` (revises `015`)

### Что сделать

```sql
-- Добавить поле в habits (фиксированная сумма в копейках, не процент!)
-- DEFAULT 0 = для существующих клубов работает по-старому (всё в фонд).
ALTER TABLE habits ADD COLUMN catcher_amount_kopecks INTEGER NOT NULL DEFAULT 0
  CHECK (catcher_amount_kopecks >= 0);
```

> **⚠️ Snapshot 2026-08-21 — критично для деплоя (review от Дмитрия):**
> **`transactions.type` — это `String(64)` (VARCHAR), НЕ Postgres ENUM.**
> Подтверждение:
> - `001_initial_schema.py:105` — `sa.Column("type", sa.String(length=64), nullable=False)`
> - `apps/backend/app/models/transaction.py:25` — `Mapped[str] = mapped_column(String(64))`
> - В БД нет Postgres TYPE с именем `transaction_type`. Валидация значений —
>   только Python-side через `TransactionType(StrEnum)` в `core/constants.py`.
>
> **Следствие:** в этой миграции НЕТ и НЕ ДОЛЖНО БЫТЬ
> `ALTER TYPE transaction_type ADD VALUE 'catcher_deposit'` — такого типа не существует,
> команда упадёт с `type "transaction_type" does not exist`.
>
> **Дополнительно:** баг из `docs/10-deploy.md §9.2` (alembic не выполняет
> `ALTER TYPE ADD VALUE` внутри транзакции, нужен workaround через `psql +
> UPDATE alembic_version`) к этой миграции **НЕ применим**, потому что
> `transaction_type` не Postgres ENUM.
>
> **Python-side добавление** нового значения `catcher_deposit` — отдельной задачей
> **Task 1.2** (правка `core/constants.py`: добавить `TransactionType.CATCHER_DEPOSIT = "catcher_deposit"`).

> **Snapshot 2026-08-21:** миграция **НЕ** удаляет `bonus_points`/`bonus_applied`/etc —
> это делает Phase 8. Здесь только ADDITIVE changes (новое поле).

### Критерий «готово»
- [ ] `make migrate-test` (upgrade → downgrade → upgrade) проходит
- [ ] `SELECT catcher_amount_kopecks FROM habits` на проде даёт 0 для всех 3 клубов
- [ ] Существующие тесты не сломались (`make test`)
- [ ] `make lint` чистый

## Task 1.2: модель `Habit.catcher_amount_kopecks` + константы

**Файл:** `apps/backend/app/models/habit.py` (добавить поле) + `apps/backend/app/core/constants.py`

### Что сделать

В `habit.py`:
```python
catcher_amount_kopecks: Mapped[int] = mapped_column(
    Integer, nullable=False, server_default="0"
)
```

В `constants.py`:
```python
class TransactionType(StrEnum):
    SUBSCRIPTION = "subscription"
    DEPOSIT_TOPUP = "deposit_topup"
    DEPOSIT_WITHDRAW = "deposit_withdraw"
    PENALTY = "penalty"
    PRIZE = "prize"
    CATCHER_DEPOSIT = "catcher_deposit"  # ← НОВОЕ

# Удалить в Phase 8: BONUS_CATCH, BONUS_SUBSCRIPTION, BONUS_POINTS
```

В `constants.py` `PenaltyConfig`:
```python
# Сумма ловцу (фиксированная, в копейках). Задаётся в Habit.catcher_amount_kopecks.
# 0 = всё в фонд (старое поведение, для обратной совместимости).
# Валдация в Pydantic-схеме admin endpoint (ge=0), на уровне SQL — только >= 0.
# Нет MAX — если catcher_amount_kopecks >= penalty_amount, всё уходит ловцу (фонд=0).
# Нет антифрод-константы — suspicious_pairs (см. SUSPICIOUS_ASYMMETRY_THRESHOLD = 3)
# логика остаётся как была, но используется ТОЛЬКО для метки `is_suspicious_pair`
# в Penalty (variant A, см. Task 1.3). Деньги НЕ блокируются.

# В Phase 8 УДАЛИТЬ: CATCHER_BONUS_POINTS, FUND_SHARE, BONUS_POINTS_EXPIRY_*
```

### Критерий «готово»
- [ ] `Habit.catcher_amount_kopecks` доступен в коде, default=0
- [ ] `TransactionType.CATCHER_DEPOSIT` импортируется
- [ ] Существующие тесты не сломались

## Task 1.3: рефактор `PenaltyService.apply_catch` — разделение штрафа

**Файл:** `apps/backend/app/services/penalty_service.py:78-274`

### Что сделать

Заменить текущий блок (lines 197-274 — списание + prize_pool + bonus_points) на новую логику:

```python
# === БЫЛО (старый код, snapshot 2026-08-21): ===
# violator_user.deposit_balance -= amount
# await self._habit_repo.add_to_prize_pool(str(habit.id), amount)
# grant_catcher_bonus = not await self._suspicious_repo.lookup_flagged(...)
# penalty = Penalty(
#     ...,
#     catcher_bonus_points=PenaltyConfig.CATCHER_BONUS_POINTS if grant_catcher_bonus else 0,
#     ...
#     bonus_applied=False,
# )

# === СТАЛО (catcher deposit share): ===
# Расчёт долей (целочисленная арифметика, никакого float)
# penalty_amount — фиксированный штраф из Habit.penalty_amount (копейки)
# catcher_amount_kopecks — фиксированная сумма ловцу из Habit.catcher_amount_kopecks (копейки)
# Сумма долей точно = penalty_amount (без остатка):
#   catcher_amount + fund_amount = penalty_amount
catcher_amount = min(habit.catcher_amount_kopecks, penalty_amount)
fund_amount = penalty_amount - catcher_amount  # остаток в фонд

# Списание с депозита нарушителя — одной суммой
violator_user.deposit_balance -= penalty_amount
if violator_user.deposit_balance < 0:
    violator_user.deposit_balance = 0  # защита от перерасхода (WAIVED-логика)

# Lock catcher user под единой транзакцией
# Порядок lock'ов: ASC по user_id — избежание deadlock'а с другими catch'ами
# (захватываем ОБА lock'а ДО логики, см. Lock-порядок ниже)
catcher_user = None
if catcher_amount > 0 and catcher_membership_id is not None:
    from app.repositories.user_repository import UserRepository
    catcher_user_obj = await self._user_repo.get(catcher_user_id)
    if catcher_user_obj is not None:
        catcher_user = catcher_user_obj

# Зачисление ловцу (если есть доля)
if catcher_amount > 0 and catcher_user is not None:
    catcher_user.deposit_balance += catcher_amount
    # Transaction для истории (audit)
    catcher_tx = Transaction(
        id=str(uuid4()),
        user_id=catcher_user.id,
        type=TransactionType.CATCHER_DEPOSIT.value,
        amount=+catcher_amount,
        balance_after=catcher_user.deposit_balance,
        related_penalty_id=penalty.id,
        related_membership_id=catcher_membership_id,
    )
    self._session.add(catcher_tx)

# В призовой фонд клуба
if fund_amount > 0:
    await self._habit_repo.add_to_prize_pool(str(habit.id), fund_amount)

# Suspicious pair — ТОЛЬКО МЕТРИКА для лидерборда (snapshot 2026-08-21, вариант A).
# Деньги НЕ блокируются: сговор финансово невыгоден (оба теряют деньги в текущей модели),
# но портит лидерборды — нужна метка для фильтрации фейковых поимок.
is_suspicious_pair = await self._suspicious_repo.lookup_flagged(
    catcher_membership_id, violator_membership_id
)

penalty = Penalty(
    id=str(uuid4()),
    membership_id=violator_membership_id,
    catcher_membership_id=catcher_membership_id,  # ВСЕГДА пишем (для истории)
    # amount = ФАКТИЧЕСКИ СПИСАННОЕ (клэмп ДО: min(penalty_amount, deposit)),
    # а НЕ номинал. Иначе CHECK ck_penalties_amount_equals_sum
    # (amount = catcher_amount + fund_share) не сходится при клэмпе.
    # В существующем коде (line 217) уже было `amount=amount` — оставлено
    # без изменений. fund_share переиспользует существующую колонку
    # (создана в 001_initial_schema.py), а НЕ новую fund_amount.
    amount=amount,
    catcher_amount=catcher_amount,    # ← НОВОЕ ПОЛЕ: сколько ушло ловцу
    fund_share=fund_share_amount,     # переиспользуем существующую колонку fund_share
    is_suspicious_pair=is_suspicious_pair,  # ← НОВОЕ ПОЛЕ: для лидерборда
    reason=PenaltyReason.CAUGHT,
    date=club_date,
)
# В Phase 8 УДАЛИТЬ: catcher_bonus_points, bonus_applied
```

> **Snapshot 2026-08-21 (variant A):** в Phase 1 Penalty МОЖЕТ сохранить `catcher_bonus_points`/`bonus_applied`
> как deprecated поля (default=0, false) — для обратной совместимости с существующими данными.
> Полное удаление — Phase 8.

### Lock-порядок (важно!)

Чтобы избежать deadlock при параллельных catch'ах разных юзеров:
```python
# Сортируем user_ids и lock'аем в ASC порядке
# ВАЖНО: catcher_membership_id может быть None (если бот ловит анонимно)
# — тогда лочим только violator_user (старое поведение)
user_ids_to_lock = sorted([violator.user_id, catcher_user_id]) if catcher_user_id else [violator.user_id]
for uid in user_ids_to_lock:
    await self._user_repo.lock_for_update(uid)
```

### Критерий «готово»
- [ ] `apply_catch` делит штраф на 2 части по `catcher_amount_kopecks`
- [ ] Если `catcher_amount_kopecks=0` → `catcher_amount=0`, всё в фонд (старое поведение)
- [ ] Если `catcher_amount_kopecks >= penalty_amount` → `catcher_amount=penalty_amount`, фонд=0
- [ ] **suspicious_pairs НЕ блокирует деньги** (variant A) — флаг только для лидерборда
- [ ] Под unit-тестами: `catcher_amount_kopecks = 0, 10000, 20000, 30000` (0₽, 100₽, 200₽, 300₽)
- [ ] Депозит ЛОВЦА инкрементится в той же транзакции
- [ ] Lock'и захватываются в ASC user_id порядке (deadlock-free)
- [ ] `Penalty.catcher_amount`, `Penalty.fund_amount`, `Penalty.is_suspicious_pair` заполняются
- [ ] Тест на race: 2 параллельных catch'а разных жертв от одного ловца не deadlock'ят

## Task 1.4: миграция 017 — `Penalty.catcher_amount` + `fund_amount` + `is_suspicious_pair`

**Файл:** новый `apps/backend/alembic/versions/017_penalty_split_columns.py` (revises `016`)

### Что сделать

```sql
ALTER TABLE penalties
  ADD COLUMN catcher_amount INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN fund_amount INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN is_suspicious_pair BOOLEAN NOT NULL DEFAULT false;

-- Backfill для существующих penalties (на проде их 0, но на всякий случай):
UPDATE penalties
SET catcher_amount = 0,
    fund_amount = amount,
    is_suspicious_pair = false
WHERE catcher_amount = 0 AND fund_amount = 0;
```

> **Phase 8 (cleanup):** DROP COLUMN `catcher_bonus_points`, `bonus_applied` — отдельной миграцией.

### Критерий «готово»
- [ ] `make migrate-test` проходит
- [ ] Существующие penalties имеют `catcher_amount=0, fund_amount=amount, is_suspicious_pair=false` (backfill)

## Task 1.5: admin endpoint — поле `catcher_amount_kopecks` в create/update клуба

**Файл:** `apps/backend/app/api/admin/v1/habits.py` (расширить `AdminHabitCreate`/`AdminHabitUpdate`)

### Что сделать

```python
class AdminHabitCreateRequest(BaseModel):
    ...  # существующие поля
    catcher_amount_kopecks: int = Field(
        default=0,
        ge=0,
        description="Сумма ловцу от штрафа в копейках. "
                    "0 = всё в призовой фонд (старое поведение). "
                    "Примеры: 10000 = 100₽ ловцу, 20000 = 200₽ ловцу. "
                    "Если catcher_amount_kopecks >= penalty_amount → всё ловцу, фонд=0."
    )
```

Передать в `HabitService.create_habit`/`update_habit`. Сохранить в `Habit.catcher_amount_kopecks`.

### Критерий «готово»
- [ ] POST /admin/v1/habits принимает `catcher_amount_kopecks` (валидация `ge=0`)
- [ ] PATCH /admin/v1/habits/{id} обновляет `catcher_amount_kopecks`
- [ ] GET /admin/v1/habits/{id} возвращает `catcher_amount_kopecks`
- [ ] Тест: создание клуба с `penalty_amount=30000, catcher_amount_kopecks=10000` →
        в БД 30000 и 10000

---

# Фаза 2 — Призовой фонд (Sprint 4, 1 неделя)

**Цель:** `close_season` распределяет 5 призовых мест (35/25/20/12/8%) из `Habit.prize_pool`.

## Task 2.1: snapshot `Habit.prize_pool` → `Season.prize_pool`

**Приоритет:** 🟠 High.
**Время:** 2-3 часа.
**Файл:** `apps/backend/app/services/penalty_service.py:180` + `apps/backend/app/services/season_service.py`

### Что сделать

В `apply_catch` (после `add_to_prize_pool` / в новом рефакторе Phase 1 Task 1.3) — **дополнительно** инкрементить `Season.prize_pool` текущего активного сезона:
```python
# В apply_catch после add_to_prize_pool:
active_season = await self._season_repo.get_active_for_habit(habit.id, club_date)
if active_season is not None:
    await self._season_repo.add_to_prize_pool(active_season.id, fund_amount)  # ← ТОЛЬКО fund_amount, не penalty_amount
```

> **Snapshot 2026-08-21 (product):** В `Season.prize_pool` пишется ТОЛЬКО `fund_amount` (доля фонда),
> НЕ полный `penalty_amount`. Доля ловца (`catcher_amount`) уходит напрямую ловцу, минуя сезон.

### Что нужно сначала
- Метод `SeasonRepository.get_active_for_habit(habit_id, club_date)` — найти сезон, у которого `status='active' AND habit_id=:id AND start_at <= :club_date AND end_at >= :club_date`
- Метод `SeasonRepository.add_to_prize_pool(season_id, amount)` — атомарный инкремент под `FOR UPDATE`

### Критерий «готово»
- [ ] Юнит-тест: `apply_catch` инкрементит `Season.prize_pool` НА `fund_amount` (не на полный штраф)
- [ ] Юнит-тест: если активного сезона нет — `Habit.prize_pool` всё равно инкрементится на `fund_amount`
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

## Task 2.3: `close_season` распределяет по 5 местам (35/25/20/12/8%) + зачисление на депозит победителя

> **⚠️ Snapshot 2026-08-20.** Реальная структура — **гибкая**: `prize_rules_snapshot`
> это `{"rules": [{"metric": str, "rank_from": int, "rank_to": int, "percentage_bp": int}, ...]}`.
> Нет хардкода "5 мест". `BASIS_POINTS_TOTAL = 10_000` уже есть, идемпотентность
> под `FOR UPDATE` уже есть.

> **⚠️ Snapshot 2026-08-21 (product).** Призы теперь **зачисляются на `User.deposit_balance`**
> победителя (а не остаются "внешним долгом"). Это требует lock_for_update на user
> победителя + обновление `balance_after`.

**Файл:** `apps/backend/app/services/season_service.py:60-122`

### Что сделать

Не "заменить `BASIS_POINTS_TOTAL`" — она уже правильная. А **создать дефолтные правила**
(если `prize_rules_snapshot` пустой) — 5 мест по 35/25/20/12/8% из финансовой механики
(`docs/archive/2026-summer-fixes/4_finansovaya_mehanika_shtrafov_i_prizov.md §3`):

```python
DEFAULT_PRIZE_RULES = [
    {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage_bp": 3500},  # 35%
    {"metric": "streak", "rank_from": 2, "rank_to": 2, "percentage_bp": 2500},  # 25%
    {"metric": "streak", "rank_from": 3, "rank_to": 3, "percentage_bp": 2000},  # 20%
    {"metric": "streak", "rank_from": 4, "rank_to": 4, "percentage_bp": 1200},  # 12%
    {"metric": "streak", "rank_from": 5, "rank_to": 5, "percentage_bp":  800},  # 8%
]
# Сумма = 10000 bp = 100% (без остатка)
```

**Дополнительно (snapshot 2026-08-21):** в `close_season` — после расчёта `share` для каждого
победителя, **зачислить на `User.deposit_balance`** под `lock_for_update`:

```python
# В SeasonService.close_season, внутри цикла for entry in ranked:
#   (было: только Transaction(type=PRIZE, amount=share) — бухгалтерская запись)
# (стало: + инкремент User.deposit_balance под lock'ом)
from app.repositories.user_repository import UserRepository
user_repo = UserRepository(self._session)

for entry in ranked:
    winner_user = await user_repo.lock_for_update(entry["user_id"])
    winner_user.deposit_balance += share  # ← НОВОЕ: зачисление на депозит
    
    tx = Transaction(
        id=str(uuid4()),
        user_id=winner_user.id,
        type=TransactionType.PRIZE.value,
        amount=+share,  # ← БЫЛО amount=share (положительный уже, не меняем знак)
        balance_after=winner_user.deposit_balance,  # ← НОВОЕ: для аудита
        related_membership_id=entry["membership_id"],
    )
    self._session.add(tx)
    distributed += share
```

> **Важно — deadlock prevention:** в `close_season` победители lock'аются в порядке
> ASC `user_id` (см. Phase 1 Task 1.3 — тот же контраст с `apply_catch`).

### Критерий «готово»
- [ ] Юнит-тест: `close_season` для фонда 15 000₽ + DEFAULT правила → 1 место 5250₽, 2 место 3750₽, 3 место 3000�, 4 место 1800₽, 5 место 1200₽
- [ ] **Юнит-тест (NEW):** `winner_user.deposit_balance` инкрементится на свою долю
- [ ] **Юнит-тест (NEW):** `Transaction(type=PRIZE, amount=+share, balance_after=...)` создаётся
- [ ] Юнит-тест: пустой фонд → 0 выплат (или по сценарию rollover)
- [ ] Юнит-тест: кастомные правила в `prize_rules_snapshot` (например, 3 места 50/30/20) — применяются вместо дефолтных
- [ ] Lock'и в ASC user_id порядке (deadlock-free для параллельных close_season разных клубов)

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

# Фаза 8 — Cleanup bonus (NEW 2026-08-21, 1-2 дня)

**Цель:** полностью удалить старую бонусную механику (`bonus_points`, `BonusService`,
`apply_catch_bonus` task, `BonusRule` и связанные транзакции). Phase 1 (catcher deposit share)
уже работает — теперь чистим то, что осталось.

> **⚠️ Snapshot 2026-08-21 (product-changes).** Phase 8 добавлена после решения
> Дмитрия полностью отказаться от виртуальных бонусов. Phase 8 идёт **сразу после Phase 1**
> (catcher deposit share), ДО Phase 3. Логика: новая механика работает → старая удаляется,
> → чистая кодовая база для Phase 3 (Character & Stats).

## Task 8.1: миграция 018 — DROP bonus columns + DROP bonus_rules таблица

**Файл:** новый `apps/backend/alembic/versions/018_drop_bonus_mechanics.py` (revises `017`)

### Что сделать

```sql
-- 1. DROP COLUMNs в users
ALTER TABLE users DROP COLUMN bonus_points;
ALTER TABLE users DROP COLUMN bonus_points_updated_at;

-- 2. DROP COLUMNs в memberships
ALTER TABLE memberships DROP COLUMN bonus_points;

-- 3. DROP COLUMNs в penalties
ALTER TABLE penalties DROP COLUMN catcher_bonus_points;
ALTER TABLE penalties DROP COLUMN bonus_applied;

-- 4. DROP TABLE bonus_rules (если больше никто не ссылается)
DROP TABLE IF EXISTS bonus_rules;

-- 5. Удалить значения BONUS_* из TransactionType StrEnum в Python (Task 8.2):
--    В БД `transactions.type` — VARCHAR(64), Postgres ENUM не используется.
--    Никаких ALTER TYPE ... DROP VALUE не нужно — старые значения остаются
--    в истории (но в Python-коде больше не используются и не валидируются).
```

> **Snapshot 2026-08-21:** на проде сейчас 4 транзакции, все типа SUBSCRIPTION/DEPOSIT_TOPUP.
> Никаких `bonus_catch`/`bonus_subscription`/`bonus_points` транзакций в проде нет
> (потому что #1 не закрыт, `apply_catch_bonus` не вызывается). DROP безопасен.

### Критерий «готово»
- [ ] `make migrate-test` проходит (upgrade → downgrade → upgrade)
- [ ] На проде: `\d users` не показывает `bonus_points`
- [ ] На проде: `\d penalties` не показывает `catcher_bonus_points`/`bonus_applied`
- [ ] На проде: `\dt bonus_rules` → "did not find any relation"

## Task 8.2: удалить `BonusService` + `BonusRuleRepository`

**Файлы:**
- `apps/backend/app/services/bonus_service.py` → **DELETE**
- `apps/backend/app/repositories/bonus_rule_repository.py` → **DELETE**
- `apps/backend/app/models/auxiliary.py` — **удалить класс `BonusRule` (lines 53-62)**
- `apps/backend/app/core/constants.py` — **удалить `TransactionType.BONUS_*`** (3 значения) +
  **удалить `PenaltyConfig.CATCHER_BONUS_POINTS`**, `PenaltyConfig.FUND_SHARE`,
  `PenaltyConfig.BONUS_POINTS_EXPIRY_*`

### Критерий «готово»
- [ ] `grep -rn "bonus_service\|BonusService\|bonus_rule_repository\|BonusRuleRepository" apps/backend/app/` → 0 совпадений
- [ ] `grep -rn "BONUS_CATCH\|BONUS_SUBSCRIPTION\|BONUS_POINTS\|CATCHER_BONUS_POINTS\|FUND_SHARE\|BONUS_POINTS_EXPIRY" apps/backend/app/` → 0 совпадений
- [ ] `make lint` чистый

## Task 8.3: удалить worker tasks `apply_catch_bonus` / `expire_bonus_points` / `integrity_check_bonus_transactions`

**Файлы:**
- `apps/worker/worker/tasks/apply_catch_bonus.py` → **DELETE**
- `apps/worker/worker/tasks/expire_bonus_points.py` → **DELETE**
- `apps/worker/worker/tasks/integrity_check_bonus_transactions.py` → **DELETE**
- `apps/worker/worker/celery_app.py` — **удалить 3 строки** в `include=[]` (lines 44, 46)
  и 2 записи в `beat_schedule` (`expire_bonus_points_daily`, `integrity_check_*`)
- `apps/backend/app/services/celery_producer.py` — **удалить** `"apply_catch_bonus": "worker.tasks.apply_catch_bonus.run"`
  из `_TASK_NAMES` (Phase 1 уже не нужна эта задача)

### Удалить тесты:
- `apps/worker/tests/test_apply_catch_bonus.py` → **DELETE**
- `apps/worker/tests/test_expire_bonus_points.py` → **DELETE**
- `apps/worker/tests/test_integrity_check_bonus_transactions.py` → **DELETE**

### Критерий «готово»
- [ ] `ls apps/worker/worker/tasks/ | grep -i bonus` → пусто
- [ ] `grep -rn "apply_catch_bonus\|expire_bonus_points\|integrity_check_bonus" apps/` → 0 совпадений
- [ ] `celery_app.py` `include=[]` без bonus-задач
- [ ] `make test` (worker) проходит (384+ тестов, без bonus-тестов)

## Task 8.4: удалить frontend bonus-ссылки

**Файлы:**
- `apps/frontend/src/shared/types/index.ts` (line 25) — **удалить** `bonus_points: number;`
- `apps/frontend/src/shared/utils/format.ts` (lines 61-63) — **удалить** 3 метки
  (`bonus_catch: "Бонус за поимку"`, `bonus_subscription: "Бонус за подписку"`,
  `bonus_points: "Бонусные баллы"`)

### Возможно, в LeaderboardPage:
- Удалить таб/ссылку на `bonus_points` (если есть)
- Заменить на новый таб «💰 Заработал на ловлях» (опционально, отдельная задача)
- `catches_count` лидерборд — ОСТАЁТСЯ

### Критерий «готово»
- [ ] `grep -rn "bonus_points\|bonus_catch\|bonus_subscription" apps/frontend/src/` → 0 совпадений
- [ ] `make lint` чистый (vitest + eslint)
- [ ] Если был отдельный таб в LeaderboardPage — он удалён или переименован

## Task 8.5: обновить документацию

**Файлы:**
- `docs/archive/2026-summer-fixes/4_finansovaya_mehanika_shtrafov_i_prizov.md` —
  добавить секцию "Снимок 2026-08-21: ловец получает реальные деньги"
- `docs/06-data-model.md` §6 — удалить раздел про `bonus_points`, добавить раздел
  про `Habit.catcher_amount_kopecks` и `TransactionType.CATCHER_DEPOSIT`
- `apps/frontend/docs/STATUS.md` — удалить упоминания bonus_points (если есть)
- `docs/AGENT_BOOTSTRAP.md` §9 — удалить "🟡 Manual catch bonus" из известных ограничений

### Критерий «готово»
- [ ] По всем перечисленным файлам — расхождений с реальным кодом нет
- [ ] Per `AGENTS.md §12` — точечные правки, не переписывание

## Task 8.6: интеграционный тест — полный сценарий с деньгами

**Файл:** новый `apps/backend/tests/integration/test_catcher_deposit_e2e.py`

### Что сделать

Полный сценарий от конца до конца:
1. Создать клуб с `penalty_amount=30000, catcher_amount_kopecks=10000` (штраф 300₽, ловцу 100₽)
2. Юзер A вступает, кладёт депозит 1000₽
3. Юзер B вступает, кладёт депозит 1000₽
4. B не делает чек-ин, A ловит B
5. **Проверить:**
   - B.deposit_balance -= 300� (30000 копеек)
   - A.deposit_balance += 100₽ (10000 копеек)
   - Habit.prize_pool += 200₽ (20000 копеек)
   - Penalty.amount = 30000, catcher_amount = 10000, fund_amount = 20000
   - Transaction(type=PENALTY, amount=-30000) для B
   - Transaction(type=CATCHER_DEPOSIT, amount=+10000) для A

### Критерий «готово»
- [ ] Тест проходит локально
- [ ] Тест **падает** если `catcher_amount_kopecks=0` (старое поведение, всё в фонд) → 0₽ ловцу
- [ ] Тест **падает** если `catcher_amount_kopecks >= penalty_amount` (всё ловцу) → фонд=0
- [ ] **Тест с suspicious_pairs:** деньги ВСЁ РАВНО переводятся (variant A), но
        `Penalty.is_suspicious_pair=true` для лидерборда
- [ ] CI зелёный

---

# Фаза 7 — Growth (2-3 недели)

**Цель:** cold start. Без пользователей продукт мёртв.

## Task 7.1: партнёрский кабинет (MVP)

**Источник:** `docs/archive/2026-summer-fixes/1_kabinet_partnera_MVP.md`

### Что сделать
- Mini App `cabinet.prideclub.fun` (или `/cabinet` маршрут в основном Mini App)
- Трекинг рефералов: `GET /api/v1/partners/me/referrals`
- Статус выплат
- Базовая аналитика

### Критерий «готово»
- [ ] Партнёр видит своих рефералов + начисленные бонусы

## Task 7.2: реферальная программа (30% lifetime revenue share)

**Источник:** `docs/archive/2026-summer-fixes/1_kabinet_partnera_MVP.md`

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

**Источник:** `docs/archive/2026-summer-fixes/3_zapusk_cherez_liderov_soobshestv_checklist.md`

### Что сделать
- Найти 10-20 лидеров сообществ (по `docs/archive/2026-summer-fixes/2_poisk_partnerov_instagram.md`)
- Предложить бесплатный доступ основателям
- Метрики успеха: конверсия, % чек-инов, retention

### Критерий «готово»
- [ ] 10 клубов создано через лидеров
- [ ] Retention > 50% за месяц

---

# Глобальные инварианты (применимы ко всем задачам)

> Из `AGENTS.md` + `docs/04-code-standards.md` + `docs/06-data-model.md`:

1. **Деньги — `int` копейки** (`Penalty.amount`/`Penalty.catcher_amount`/`Penalty.fund_amount`,
   `Transaction.amount`, `Habit.price_month`/`Habit.penalty_amount`/`Habit.catcher_amount_kopecks`,
   `User.deposit_balance`, `UserStats.value` — отдельная ось, не деньги, но тоже `BIGINT`).
   Basis points (`percentage_bp` в `prize_rules_snapshot`) — `int` в диапазоне `[0, 10_000]`.
   `catcher_amount_kopecks` — НЕ basis points, это фиксированная сумма в копейках (`ge=0`).
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

# Карта задач (быстрый обзор, rebuild 2 от 2026-08-21)

| Фаза | Задач | Время | Блокирует прод? | Порядок |
|---|---|---|---|---|
| 0 | ~~1~~ (Task 0.1 — закрыт до создания плана) | — | ✅ закрыт `9c32d6f` | — |
| 1 | **5 (Tasks 1.1-1.5)** ✅ **DEPLOYED 2026-08-21** | **2-3 дня** | нет, но финансовая логика неполная | **✅ ЗАКРЫТА, deployed** |
| 8 | **7 (Tasks 8.1-8.6) — NEW** | **1-2 дня** | нет (косметика кода) | **после Phase 1** |
| 2 | 4 (Tasks 2.1-2.4) + **2.5 (prize → deposit)** | **3-4 дня** | нет (сезонов нет) | после Phase 3 |
| 3 | 12 (Tasks 3.1-3.12) | 2-3 недели | нет, но это центральная ТЗ-фича | **главный** |
| 4 | **4 (Tasks 4.8-4.11)** | **1-2 дня** | нет — хвост за Phase 3 | параллельно Phase 3 |
| 5 | 6 (Tasks 5.1-5.6) | 1-2 дня | нет | после Phase 2 |
| 6 | 4 (Tasks 6.1-6.4) | по 1 дню | нет (для soft-launch) | после Phase 5 |
| 7 | 3 (Tasks 7.1-7.3) | 2-3 недели | нет, но без роста нет пользователей | последний |
| **Всего** | **~45 задач** | **5-6 недель** | |

---

# С чего начать СЕГОДНЯ (rebuild 2 от 2026-08-21)

**Пересобранный порядок:** Phase 1 (Catcher deposit share) → **Phase 8 (Cleanup bonus)** → **Phase 3 + хвост Phase 4 параллельно** → Phase 2 (призы → депозит) → Phase 5 → Phase 6 → Phase 7.

**Первая задача сегодня — Task 1.1** (миграция 016 — `Habit.catcher_amount_kopecks` + новая транзакция `CATCHER_DEPOSIT`).

```bash
# 1. Создать ветку для Task 1.1
git checkout -b feat/catcher-deposit-share-task-1-1

# 2. Создать файл apps/backend/alembic/versions/016_habit_catcher_amount.py:
#    - ALTER TABLE habits ADD COLUMN catcher_amount_kopecks INTEGER NOT NULL DEFAULT 0
#      CHECK (catcher_amount_kopecks >= 0)
#    ВАЖНО: НЕ добавлять ALTER TYPE transaction_type ADD VALUE — transactions.type
#    это VARCHAR(64), а не Postgres ENUM. См. snapshot в Task 1.1.
#    Python-сторона (TransactionType.CATCHER_DEPOSIT) добавляется в Task 1.2.

# 3. Тест миграции:
make migrate-test

# 4. Commit
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev commit -am "feat(penalty): add Habit.catcher_amount_kopecks (Task 1.1)"

# 5. Push + deploy (по отдельному "ок" пользователя)
```

**Phase 1 целиком** (Tasks 1.1+1.2+1.3+1.4+1.5, 2-3 дня):
- 1.1: миграция (1-2 ч)
- 1.2: модель + константы (30 мин)
- 1.3: рефактор `apply_catch` (4-6 ч, основная работа)
- 1.4: миграция для `Penalty.catcher_amount`/`fund_amount` (1 ч)
- 1.5: admin endpoint с `catcher_amount_kopecks` (2-3 ч)

**После Phase 1 — Phase 8 (Cleanup bonus)** (1-2 дня). Удаляем всю старую бонусную механику. Новая уже работает.

**После Phase 8 — Phase 3** (Character & Stats, 2-3 недели). Это самая длинная и важная фаза, в ней же делаются Tasks 4.8/4.9/4.10/4.11 параллельно (всё равно они требуют API из Phase 3).

**После Phase 3 — Phase 2** (призовой фонд + зачисление на депозит победителей, 3-4 дня). Метрика `stat_value` для топ-5 победителей сезона появится только с Phase 3, поэтому Phase 2 логичнее делать после.

**После Phase 2 — Phase 5** (техдолг, 1-2 дня). После Phase 5 — **Phase 6** (deploy ops, по 1 дню) и **Phase 7** (growth, 2-3 недели).

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
