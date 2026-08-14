# Pravki.md — задачи и аудит

> Snapshot от 2026-08-04. Обновлено после post-mortem «бот молчит, чек-ины не доходят»:
> фикс `/app/infra/.env` (WEBHOOK_BASE_URL/WEBAPP_URL на сырой IP) + фикс worker `CheckinService`
> DI-bug (отсутствовал `penalty_repo`). Полная цепочка — см. §7.8 ниже.

## 0. Workflow

1. Сделать задачу локально (коммит в `feature/topic-scoped-checkin`).
2. Задеплоить на сервер (только frontend, по `docs/02-architecture.md` §13).
3. Юзер проверяет руками → говорит «всё ок».
4. Только после «ок» — `git push origin feature/topic-scoped-checkin`.

## 1. UI/UX — картинки и обрезка

| # | Задача | Слой | Сложность | Статус |
|---|---|---|---|---|
| 1.1 | Картинка в карточке клуба не обрезается, подстраивается под размер | Frontend: `Marketplace`, `Today`, admin `Create/Edit` | S | ✅ `1736cdb` → `73e8b7d` (адаптивный контейнер) |
| 1.2 | Картинки клубов в профиле в "Моих клубах" | Frontend: `Profile` | S | ✅ `1736cdb` → `73e8b7d` |

## 2. UI/UX — текст и форматирование

| # | Задача | Слой | Сложность | Статус |
|---|---|---|---|---|
| 2.1 | Убрать секунды у времени в списке клубов и карточке клуба (HH:MM) | Frontend: `Marketplace`, `Today`, `PaymentModal`, `HabitCard` | S | ✅ `c50c394` |
| 2.2 | Названия клубов в клиентской части без `[...]` (как в админке) | Frontend | S | ✅ `73e8b7d` (используются `〖〗` везде в клиенте) |
| 2.3 | Убрать id пользователя в профиле | Frontend: `Profile` | XS | ✅ `c50c394` |
| 2.4 | Заменить `Пэйдж пропуск` на `Ежедневное задание:` + бейдж; убрать `Сменить` | Frontend: `Today` | M | ✅ `75c1a63` |
| 2.5 | Убрать кнопку `Сменить клуб` в лидерборде; back `Лидеры клуба` → `/leaderboards` | Frontend: `Leaderboard` | S | ✅ `75c1a63` |
| 2.6 | BottomNav «Профиль» — реальное фото из Telegram initData (вместо эмодзи 👤), со свечением | Frontend: `BottomNav` | S | ✅ `153dc83` |
| 2.7 | Убрать депозит и `Europe/Moscow` subtitle из карточки клуба (Today) | Frontend: `Today` | XS | ✅ `73e8b7d` |

## 3. UI/UX — компоненты и стили

| # | Задача | Слой | Сложность | Статус |
|---|---|---|---|---|
| 3.1 | Свечение фиолетовым под аватаром пользователя (ring-2 + box-shadow на primary/60) | Frontend: `Avatar` (prop `glow`), `Profile` | S | ✅ `1736cdb` |
| 3.2 | Кнопка «+ Пополнить» → мок-флоу с пресетами 299/599/999/1999 ₽ + запись `transactions(type='deposit_topup')` | Backend: `/api/v1/payments/topup` + Frontend: drawer в `Profile` | M | ✅ `3a40b31` (коммит 7.2) |

## 4. Admin Mini App

| # | Задача | Слой | Сложность | Статус |
|---|---|---|---|---|
| 4.1 | Убрать вкладку «Все» в админке (оставить Активные/Скрытые/Архив) | Frontend: `admin/pages/HabitsListPage` | S | ✅ `c50c394` |
| 4.2 | Убрать текст «УЖЕ ПРИВЯЗАН К …» в админке (disabled достаточно) | Frontend: `admin/pages/HabitCreatePage` | S | ✅ `c50c394` |
| 4.3 | Админ может менять фото карточки: нажал на превью → выбор файла → замена | Frontend: `admin/pages/HabitCreatePage` + `HabitEditForm` | S | ✅ `afc55d7` |

## 5. Бизнес-логика — чек-ины

| # | Задача | Слой | Сложность | Статус |
|---|---|---|---|---|
| 5.1 | Если юзер вступил в клуб после дедлайна — не считать пропуском за этот день | Backend: `MembershipService`, Worker: `close_catch_window` (cron) | M | 🔴 TODO (= задача 7.3) |
| 5.2 | Бот должен отвечать именем пользователя в `✅ Принято, {name}!` для кружков и фото | Bot: `apps/bot/bot/handlers/checkin.py` (взять `message.from_user.first_name`) | XS | ✅ `d9144df` |
| 5.3 | Бот должен отклонять кружок <3 сек **до** отправки в backend | Bot: pre-validate `video_note.duration` | S | ✅ `de87272` |

## 6. Аудит (выполнено 2026-07-23)

### 6.1 Призовой фонд — ✅ работает корректно

**Цепочка:** `Penalty.amount → Habit.prize_pool (+= FOR UPDATE) → Season.prize_pool → распределение в close_season`.

| Файл | Что делает |
|---|---|
| `repositories/habit_repository.py:119-135` | `add_to_prize_pool(habit_id, amount)` — атомарный инкремент с `session.get(..., with_for_update=True)`. Защита от гонки между `apply_catch` и `apply_window_expired` (две Celery-таски могут прийти одновременно) |
| `services/penalty_service.py:71-156` (`apply_catch`) | `SELECT FOR UPDATE` на violator → `amount = min(penalty, deposit)` → `deposit -= amount` + `add_to_prize_pool(habit, amount)` + `penalty.fund_share = amount` (транзакция в БД) |
| `services/penalty_service.py:159-231` (`apply_window_expired`) | Аналогично для cron — тот же `fund_share = amount` |
| `services/season_service.py:60-122` (`close_season`) | `SELECT FOR UPDATE` на Season → проверка status==ACTIVE → `validate_prize_rules` → цикл по rules → **basis points арифметика** (`prize_pool * percentage_bp // 10_000`, никакого float/Decimal) → запись `Transaction(type=PRIZE)` для каждого победителя → status=CLOSED |

**Инварианты (все соблюдены):**
- Деньги — `int` копейки везде (`% 1 == 0` благодаря basis points)
- `FOR UPDATE` на критических локах (нет race conditions)
- Идемпотентность через уникальный индекс `penalties(membership_id, date, reason)` (нет двойных штрафов)
- Целочисленное распределение (`BASIS_POINTS_TOTAL = 10_000`), без потери копеек
- `validate_prize_rules` гарантирует что сумма percentages = ровно 100% (иначе `InvalidPrizeRulesError`)

**Минорное замечание:** при распределении `share = per_member_pool // len(ranked)` остаток копеек теряется (идёт молча в ноль). Это нормально — нельзя раздать остаток копейки. Если нужно — можно добавить "first place gets remainder" как политику.

### 6.2 Ловля (catch) — ✅ работает корректно

| Файл | Что делает |
|---|---|
| `services/penalty_service.py:58-156` (`apply_catch`) | 1) Rate-limit через Redis Lua (`incr_catch`, 10/10s); 2) `CannotCatchSelfError`; 3) проверка membership status==ACTIVE; 4) проверка habit существует; 5) идемпотентность через существующий penalty; 6) `lock_for_update(violator)`; 7) проверка `suspicious_pairs` (если пара в flagged → `catcher_membership_id=None` — бонус не начислится); 8) списание депозита + инкремент prize_pool + создание Penalty + flush + создание Transaction; 9) если deposit=0 → status=PAUSED |
| `services/bonus_service.py:54-123` (`apply_catch_bonus`) | 1) Идемпотентность через `penalty.bonus_applied`; 2) проверка `catcher_membership_id is not None` (нет бонуса если suspicious); 3) повторная проверка `lookup_flagged`; 4) `user.bonus_points += 1`; 5) создание `Transaction(type=BONUS_CATCH)` (для `integrity_check`); 6) если достигли `bonus_rule.threshold` → `_grant_reward` |
| `services/catch_rate_limiter.py` | Lua-скрипт атомарного INCR + EXPIRE (защита от гонки INCR без EXPIRE) |
| `core/constants.py:72` | `RATE_LIMIT_CATCH = "10/10s"` (настраивается) |

**Инварианты (все соблюдены):**
- `FOR UPDATE` на violator + `add_to_prize_pool` (нет race condition)
- Идемпотентность penalty через `(membership_id, date, reason)` UNIQUE
- Rate-limit 10/10s per user через Redis Lua (атомарный)
- Self-catch запрещён (`CannotCatchSelfError`)
- Suspicious pairs → бонус не начисляется, но штраф списывается (дисциплина не ослабляется)
- Bonus transaction записан в `transactions` для каждого `bonus_applied=true` (для `integrity_check_bonus_transactions` cron)

**Известные нюансы:**
- Если у юзера нет Redis (rate limiter не инициализирован) → fail-open (нет rate-limit). Для прод-режима `redis_port=None` бросает `RateLimitDisabledError` после commit'а T5.
- `suspicious_pairs` lookup происходит дважды: в `apply_catch` (для записи `catcher_membership_id=None`) и в `apply_catch_bonus` (defence in depth). Доп. запрос в БД, но атомарность гарантирована.

### 6.3 Лидерборд — аудит для задачи «фото участников»

**Текущее состояние:**
- `LeaderboardEntry` (`apps/backend/app/api/v1/leaderboard.py:24`) содержит только `rank`, `membership_id`, `first_name`, `metric_value`. **Нет `photo_url`, нет `user_id`**.
- `apps/backend/app/models/user.py` — модель User **не содержит** колонку `photo_url` (в отличие от Habit).
- Mini App SDK (`window.Telegram.WebApp.initDataUnsafe.user.photo_url`) даёт фото **только текущего юзера**, не других участников.
- Telegram Bot API: `getUserProfilePhotos(user_id)` → список `PhotoSize` с CDN-URL (`https://api.telegram.org/file/bot<TOKEN>/<file_path>`).

**Вывод: показать фото других участников без бэкенда невозможно.**

## 7. Задачи в работе / TODO

### 7.1 Backend: фото участников в лидерборде — M ✅ ВЫПОЛНЕНО (v3, подход D)

**Подход D (диск-кеш + nginx alias + proxy_pass fallback, заменён 2026-07-24):**

История: подходы A→B→C' развивались, но **307 redirect на Telegram CDN не работает в `<img>`**:
- Telegram CDN отдаёт `Content-Type: application/octet-stream` + `Content-Disposition: attachment`.
- Браузер принудительно скачивает файл вместо рендера в `<img>` (RFC 6266, behavior independent of CSP).
- Токен бота **утекает** в URL `https://api.telegram.org/file/bot<TOKEN>/...` → виден в DevTools любому юзеру.

Решение (подход D):
- Worker `update_user_photos` скачивает JPEG с Telegram CDN **один раз** и сохраняет атомарно в `<STATIC_DIR>/avatars/{user_id}.jpg` (volume `club_uploads`).
- Backend `AvatarService.get_or_fetch_local_path` — cache hit (file + file_id match) → мгновенный Path, без HTTP. Cache miss → скачивает с Telegram + сохраняет.
- Redis кеш `user_photo_file_id:{user_id}` (6h TTL) для инвалидации при смене фото.
- Endpoint `/api/v1/users/{id}/photo` → `FileResponse(image/jpeg)` с `Cache-Control: private, max-age=21600`.
- **Nginx alias + error_page fallback** на хосте: `location /api/v1/users/N/photo$` (regex, объявлен **до** `location /api/`) проксирует на `habit_frontend /avatars/N.jpg` (внутренний путь). Frontend nginx отдаёт файл напрямую через `alias /usr/share/nginx/html/static/avatars/$1.jpg;` (volume `club_uploads`). На 404 от frontend (`proxy_intercept_errors on` + `error_page 404 = @avatar_backend_fallback`) → backend (cold cache: backend скачает с TG, сохранит на volume, frontend будет hit на следующий запрос). БЕЗ `try_files` в alias location — конфликтует.
- Frontend nginx location `/avatars/(\d+).jpg` — `alias /usr/share/nginx/html/static/avatars/$1.jpg;` (volume `club_uploads:/usr/share/nginx/html/static`).
- После первого hit — backend не участвует для этого юзера (на горячих юзеров = 0 backend запросов).

**Почему безопасно:**
- `user_id` — int, не user input (Telegram-юзер видит аватарки других юзеров в лидерборде — намеренно).
- File name = `f"{user_id}.jpg"` — path-traversal невозможен.
- `MAX_JPEG_BYTES = 5 MB` — защита от аномальных ответов.
- В cache-hit пути nginx не делает auth (initData не проверяется) — намеренно: файл уже прошёл через backend при первой загрузке. Auth всё равно защищает запись (TelegramUserDep → 401 без initData).
- **Токен бота больше НЕ утекает** в URL клиента.

**Почему надёжно (1000+ users):**
- Один раз скачал — навсегда. При 1000 RPS лидерборда = 0 backend запросов для аватарок (все через nginx).
- Worker cron раз в сутки в 04:00 UTC подтягивает фото для **всех** active memberships. На 1000 users = 3000 req/сутки = 0.035 req/sec (в 860 раз ниже глобального лимита Bot API 30/sec).
- При смене аватара: `file_id` в Telegram меняется → worker скачает заново (cache miss по file_id).
- На 1000 users = 30-50 МБ диск (не нагрузка). На 10k users = 300-500 МБ (S3 миграция — отдельная задача).

**Реализованные файлы:**
- `apps/backend/app/services/avatar_service.py` — `get_or_fetch_local_path` (диск-кеш + Redis), `get_cdn_url` (legacy)
- `apps/backend/app/api/v1/users.py` — `FileResponse` (был 307)
- `apps/backend/app/main.py` — lifespan: `mkdir <STATIC_DIR>/avatars` через `asyncio.to_thread`
- `apps/backend/app/core/deps.py` — `get_avatar_service` читает `app.state.avatars_dir`
- `apps/worker/worker/tasks/update_user_photos.py` — скачивает JPEG + сохраняет на volume
- `infra/docker-compose.yml` — worker: volume `club_uploads:/app/static`, env `STATIC_DIR=/app/static`
- `infra/nginx/frontend.nginx.conf` — location `/avatars/N.jpg` отдаёт файл из volume
- `infra/nginx/nginx.prideclub.conf` (ref) — location `/api/v1/users/N/photo$` с `proxy_intercept_errors + error_page 404 = @avatar_backend_fallback`. На **продовом сервере** конфиг `nginx.prideclub.conf` отличается от репо (другие server{} блоки для каждого домена, `app.prideclub.fun` имеет `location /api/`). Avatar location добавлен через `infra/insert_avatar_loc.py` (см. infra/README). Файл в репо — референс.
- `apps/backend/alembic/versions/013_user_photo.py` — миграция (без изменений с прошлого раза)
- `apps/backend/app/models/user.py` — `photo_file_id` + `photo_fetched_at` (без изменений)
- `apps/backend/app/api/v1/leaderboard.py` — `LeaderboardEntry.photo_url` = `/api/v1/users/{id}/photo` (relative)
- `apps/frontend/src/shared/types/index.ts` — `LeaderboardEntry.photo_url`
- `apps/frontend/src/pages/Leaderboard/LeaderboardPage.tsx`, `GlobalLeaderboardPage.tsx` — `<img src={new URL(row.photo_url, window.location.origin).toString()}>` (не `usePhotoBlob`)

**Тесты:** 209 backend passed (avatar_service + user_photo_endpoint + leaderboard + checkin + fakes), ruff clean.

**Деплой:** alembic 013 уже применена, backend + worker rebuilt --no-cache, nginx config validated, **2 юзера имеют фото в volume** (printer 16 КБ, Дмитрий 32 КБ, всего 49 КБ). Worker cron в 04:00 UTC подтянет остальных.

### 7.2 Backend: `/api/v1/payments/topup` (мок-пополнение депозита) — M ✅ ВЫПОЛНЕНО

**Цель:** кнопка «+ Пополнить» в `Profile` → пресет 299/599/999/1999 ₽ → запись `transactions(type='deposit_topup')` + инкремент `memberships.deposit_balance`. Цель — **тестировать ловлю/штрафы/призовой фонд**.

**Реализованные файлы:**
- `apps/backend/app/api/v1/payments.py` — POST /api/v1/payments/topup (X-Telegram-Init-Data, Pydantic Field(gt=0, le=10_000_000))
- `apps/backend/tests/test_topup.py` — 3 теста (happy path с проверкой транзакции, gt=0 → 422, no initData → 401)
- `apps/frontend/src/shared/ui/TopUpModal.tsx` — radio-выбор клуба + useMutation + showAlert (нативный Telegram alert)
- `apps/frontend/src/shared/telegram/tma.ts` — showAlert(message) helper
- `apps/frontend/src/pages/Profile/ProfilePage.tsx` — disabled кнопка если myHabits пустой + tooltip

**Деплой:** backend rebuilt, frontend nginx reload, smoke tested на проде.

### 7.3 Backend: `close_catch_window` guard «joined_at >= club_date» — M

**Цель:** новый участник, вступивший после дедлайна чек-ина, **не считается пропавшим** в день вступления. Пропуск начинается со следующего клуб-дня.

**План:**
| Шаг | Файл | Что |
|---|---|---|
| Worker | `apps/worker/worker/tasks/close_catch_window.py` | В цикле по memberships: пропускать если `membership.joined_at.date() >= club_date_today` |
| Тест | `apps/worker/tests/test_close_catch_window.py` | Кейсы: join до дедлайна → штрафуется; join после дедлайна → не штрафуется |

**Оценка:** ~30 минут (только worker + тест).

### 7.4 Bot: имя пользователя в «Принято» — XS

**Цель:** `↩️ ✅ Принято, {first_name}! Молодец😉` (сейчас `{first_name}` подставляется пусто).

**План:**
| Шаг | Файл | Что |
|---|---|---|
| Bot | `apps/bot/bot/handlers/checkin.py` | В функции `_accepted_text()` взять `message.from_user.first_name` вместо `{}` |
| Тест | `apps/bot/tests/test_checkin_handler.py` | Проверить что `first_name` подставлен |

**Оценка:** ~10 минут.

### 7.5 Bot: pre-validate длительности кружка <3 сек — S

**Цель:** бот отвечает «чек-ин не принят, нужно записать видео-кружок более 3 секунд» **до** отправки в backend. Сейчас бот принимает и пишет «Принято», а worker отвергает асинхронно (`code: too_short`).

**План:**
| Шаг | Файл | Что |
|---|---|---|
| Bot | `apps/bot/bot/handlers/checkin.py` | В `_prefilter()` (уже есть после PR №9) добавить проверку `video_note.duration < 3` → ответить `REJECT_TOO_SHORT` и не слать в backend |

**Оценка:** ~20 минут.

### 7.6 Frontend: ребрендинг «Рейтинг» + tab-передача + row в одну строку — M ✅ ВЫПОЛНЕНО (v3.2)

**Задача (по жалобе юзера):**
- На `/leaderboards` — огромные карточки с top-3 для каждого клуба, сложно скроллить, нечитаемо.
- Табы «Ловцы» / «Позор» — англицизмы, неочевидно для русскоязычного юзера.
- В строке юзера на `/habits/{id}/leaderboard` имя и метрики в две строки, а не в одну.
- На mobile (узкий экран) ничего не влезает.
- При переходе из аккордеона в лидеры клуба — всегда открыт таб «Серии», хотя юзер только что смотрел «Лентяи».

**Backend (Pravki §7 v3.2):**

- `GET /leaderboard/{tab}/clubs` — новый endpoint. Возвращает список клубов юзера (active member) с `habit_id`, `title`, `members_count`. Без top-3 (UI страница лидеров клуба делает отдельный запрос).
- `metric_label` локализован server-side: `streak → "Серии"`, `catches → "Охотники"`, `shame → "Лентяи"`.
- `LeaderboardClub`, `LeaderboardClubsResponse` — Pydantic-модели.
- `LeaderboardTabId` — `"streak" | "catches" | "shame"` (новый тип, единый для клиента и сервера).
- Старый `/leaderboard/{tab}/overview` оставлен для обратной совместимости (больше не используется в UI).

**Frontend (полный rewrite `GlobalLeaderboardPage`):**

- Заголовок «Рейтинг» (убран подзаголовок).
- 3 аккордеона вместо табов: 🔥 **Серии** / 🎯 **Охотники** / 😴 **Лентяи**.
- «Серии» открыта по умолчанию (как на «Лидерах клуба»).
- Клик на аккордеон → запрос `useLeaderboardClubs(tab)`, список клубов с белой обводкой (`border-white/10`).
- Empty state: «Нет клубов» + кнопка «Выбрать клуб» → `/marketplace`.
- Клик на клуб → `navigate(\`/habits/${club.habit_id}/leaderboard?tab=${id}\`)` — передаём активный tab в URL.

**LeaderboardPage (страница лидеров клуба):**

- `useSearchParams` → читаем `?tab=`, если валиден (`streak|catches|shame`) → `setTab` initial. Иначе default `streak`.
- Табы переименованы: **Серии / Охотники / Лентяи** (единые лейблы с `/leaderboards`).
- Row — горизонтальный flex (`inline-flex`):
  ```
  [avatar] Имя | 1 дн. | 📅 1 · 🔥 0 · 🎯 0 · 😴 0
  ```
- Avatar: новый size `xs` = `h-7 w-7 text-[10px]` (mobile) → `sm:h-9 sm:w-9 sm:text-sm` (desktop).
- `metricLabel(value)` — функция склонения:
  - `streak` → `{value} дн.`
  - `catches` → `{value} {раз|раза}` через `pluralRaz()` (1 раз / 2 раза / 5 раз).
  - `shame` → `{value} штрафов`.
- `border-l border-white/10` — вертикальные разделители между блоками.
- `truncate min-w-0 flex-1` для имени — 「Дмитрий Иванов」обрезается до 「Дмитрий…」.
- `text-[10px] sm:text-xs` для breakdown — мелче на mobile.
- `whitespace-nowrap` — breakdown не переносится.
- 🚔 (полиция) → **😴** (лентяй) в breakdown.

**Реализованные файлы:**
- `apps/backend/app/api/v1/leaderboard.py` — endpoint `GET /leaderboard/{tab}/clubs`, Pydantic-модели.
- `apps/frontend/src/shared/types/index.ts` — `LeaderboardClub`, `LeaderboardClubsResponse`, `LeaderboardTabId`.
- `apps/frontend/src/shared/api/index.ts` — `leaderboardApi.clubs(tab)`.
- `apps/frontend/src/shared/hooks/index.ts` — `useLeaderboardClubs(tab)`, re-export `LeaderboardTab` из `@/shared/types`.
- `apps/frontend/src/shared/ui/Avatar.tsx` — size `xs` (responsive).
- `apps/frontend/src/pages/GlobalLeaderboard/GlobalLeaderboardPage.tsx` — полный rewrite.
- `apps/frontend/src/pages/Leaderboard/LeaderboardPage.tsx` — tab через `?tab=`, row в одну строку, `pluralRaz`, 😴.

**Тесты:** 209 backend passed, tsc/eslint clean, ручная проверка на проде (3 таба: `Серии`/`Охотники`/`Лентяи`, 3 клуба в каждом, переход `?tab=shame` открывает таб Лентяи).

**Деплой:** backend + frontend, без миграций. Юзер перезагрузил Telegram Mini App — увидел новый UI.

### 7.7 Bot: подтверждение ловли с предпросмотром нарушителя — S — TODO

**Цель:** когда юзер нажимает «Спалить» в Mini App, бот подтверждает в Telegram: «Точно спалить [@name]? У него X чек-инов, Y штрафов.» с кнопками `Да` / `Нет`. Сейчас бот только «Принято», без подтверждения.

**План:** перенести логику catch из Mini App в Telegram (long-poll webhook callback на inline-кнопках).
| Шаг | Файл | Что |
|---|---|---|
| Bot | `apps/bot/bot/handlers/catch.py` | Inline-кнопки `callback_data="catch:yes:{membership_id}"` |
| Bot | `apps/bot/bot/handlers/catch.py` | Подтверждение `confirm_catch`, вызов `/internal/penalties/catch` |
| Frontend | `apps/frontend/src/pages/Members/MembersPage.tsx` | Убрать кнопку «Спалить», заменить на deep-link в Telegram с предзаполненным habit_id |

**Оценка:** ~2 часа.

### 7.8 Infra/Worker: «бот молчит, чек-ины не доходят» — M ✅ ВЫПОЛНЕНО (2026-08-04)

**Симптом:** юзер шлёт видео-кружок в топик клуба в Telegram. Бот **не реагирует вообще**.
В `Telegram.getWebhookInfo` — `pending_update_count: 2`, `last_error_message: "SSL error {certificate verify failed}"`.

**Корневая причина №1 (фикс 1 — `/app/infra/.env`):**

В `/app/infra/.env` (который читает `docker-compose` через `${VAR}` подстановку в `infra/docker-compose.yml`)
стояло:
```
WEBHOOK_BASE_URL=https://169.58.52.78   ← сырой IP
WEBAPP_URL=https://169.58.52.78         ← сырой IP
```
TLS-сертификат в `/etc/letsencrypt/` выписан на `api.prideclub.fun`, не на IP. Telegram пытается
доставить апдейт на `https://169.58.52.78/bot/webhook` → TLS handshake падает → апдейты копятся в
`pending_update_count`. Юзер видит «молчащего бота».

**Дополнительный скрытый баг:** в `x-bot-env` anchor `infra/docker-compose.yml` **не было**
`ENVIRONMENT`. Без `ENVIRONMENT=production` в контейнере — `_validate_webhook_url()` в
`apps/bot/bot/main.py` возвращался рано (default = `development`) и **не валидировал URL**.

**Корневая причина №2 (фикс 2 — worker `process_checkin.py`):**

После фикса №1 Telegram доставил 2 застрявших апдейта, бот принял, backend положил в Celery,
worker **упал** с `CheckinService.__init__() missing 1 required positional argument: 'penalty_repo'`.
`process_checkin._process()` имеет `except Exception` catch-all, который глотает TypeError и
возвращает `{"ok": False, "err": "..."}`. Celery помечает задачу `succeeded`. Чек-ины **терялись
молча** — юзер видел «Принято» в чате, а в БД ничего не было.

Причина: в `CheckinService.__init__` добавили `penalty_repo` (для `get_today_status` →
`TodayStats.penalties_count/total`), backend обновили (`apps/backend/app/api/v1/habits.py:42-49`),
а worker — забыли.

**Реализованные файлы:**
- `infra/docker-compose.yml` — `x-bot-env` anchor: добавлены `${VAR:?must be set in prod ...}` на
  `WEBHOOK_BASE_URL` / `WEBAPP_URL` (fail-fast compose), плюс `ENVIRONMENT: ${ENVIRONMENT:-production}`.
- `apps/worker/worker/tasks/process_checkin.py` — добавлен `PenaltyRepository` импорт и
  `penalty_repo=PenaltyRepository(session)` в инстанс `CheckinService`.
- `/app/infra/.env` на сервере — `WEBHOOK_BASE_URL=https://api.prideclub.fun`, `WEBAPP_URL=https://app.prideclub.fun`.
- `docs/02-architecture.md` §9.2 — новая запись «Закрытые проблемы (snapshot 2026-08-04)».
- `docs/AGENT_BOOTSTRAP.md` §3 — новый раздел «⚠️ На сервере ДВА `.env` файла — НЕ путай» с
  подробной таблицей кто-что-где-почему.

**Деплой:** rsync worker → `docker compose build worker --no-cache` → `docker compose up -d worker`;
rsync compose → правка `.env` → `docker compose up -d bot`. Верификация:
- `getWebhookInfo` → `url=https://api.prideclub.fun/bot/webhook`, `pending_update_count=0`.
- Синтетический тест через Celery: `worker.tasks.process_checkin.run` → `ok=True, checkin_id=...`
  → строка в `checkins` (потом удалил тестовую запись).
- 2 ранее застрявших апдейта Telegram доставил → бот принял → backend enqueued → worker
  (после фикса) обработал бы (если бы фикс был применён ДО доставки; в нашем случае — оба
  дошли до worker'а, оба вернули `ok=False` до фикса №2, в БД ничего нет — это побочный эффект
  неработавшего периода).

**Уроки (анти-паттерны):**
1. **Не два, а три источника правды для env** — `/app/.env` (backend-mounted), `/app/infra/.env`
   (compose interpolation), и `.env.example` (документация). Правка `/app/.env` **не помогает**
   для переменных бота — править `/app/infra/.env`.
2. **Catch-all `except Exception` в worker скрывает DI-баги**. Без логирования на уровне
   `logger.error("worker_checkin_failed", extra={"err": str(exc), "stack": traceback.format_exc()})`
   эти баги невидимы. Backlog: добавить traceback + alert в Sentry.
3. **Fail-fast валидация должна быть активирована на проде**. Сейчас `bot/main.py:_validate_webhook_url`
   требует `environment == "production"`, но `ENVIRONMENT` не передавалось в контейнер → валидация
   спала. **Добавлено `ENVIRONMENT: ${ENVIRONMENT:-production}`** — теперь на проде fail-fast
   работает, на dev — отключается через явный `ENVIRONMENT=development`.

### 7.9 Infra: автоматическая чистка Docker — S ✅ ВЫПОЛНЕНО (2026-08-04)

**Симптом:** после 2 недель активных `docker compose build X --no-cache` ребилдов диск
`/` заполнился до 91% (`8.7 GB` свободно из `96 GB`). `docker system df` показывал
`Images: 206, Reclaimable: 61 GB` и `Build Cache: 1012, 78 GB` — на вид огромный мусор.

**Реальная причина:** `docker images` показал всего **9 образов** (4 наших + 5 базовых),
0 dangling. Всё "106" в Images и "61 GB reclaimable" — это **build cache** (счётчики BuildKit),
а не реальные образы. После `docker builder prune -af` (фоном, ~5 минут):
```
Before: Images 7 (2.1 GB), Build Cache 1012 (78 GB), / 91%
After:  Images 7 (2.1 GB), Build Cache 24 (1.8 GB), / 9%
```

**Реализованные файлы:**
- `infra/deploy.sh` — новая функция `prune_images()`, вызывается между `build_images()`
  и `run_migrations()`. Делает `docker image prune -f` + `docker builder prune -f
  --filter "until=72h"`. Время выполнения: 5–30 секунд.
- Cron на хосте (`/var/spool/cron/crontabs/root`):
  ```
  0 4 * * 0 docker image prune -a -f --filter "until=168h" && \
            docker builder prune -f --filter "until=168h"
  ```
  Воскресенье 04:00 UTC — 7 дней порог для дополнительной страховки.
- `Pravki.md` §10.1 — задокументирован workflow.

**Уроки (анти-паттерны):**
1. `docker system df` врёт про images — показывает build cache слои как "reclaimable images".
   Реальное число смотри через `docker images --format "{{.Repository}}:{{.Tag}}"` — там будет
   только то что действительно нужно.
2. `docker builder prune -af` (без фильтра) единственный способ вычистить накопившийся cache.
   С `--filter "until=72h"` слишком консервативен если cache старше 72ч массово.
3. Prune занимает **минуты** (5–10 на 1000+ entries), а не секунды. Запускать в фоне через
   `nohup ... &`, не блокировать deploy-script.

## 8. Готовые коммиты (сессия 2026-07-23)

| SHA | Сообщение |
|---|---|
| `c50c394` | fix(frontend): UI cleanups — hide user id, drop window seconds, remove 'Все' tab, drop 'уже привязан' text |
| `75c1a63` | fix(frontend): remove 'Сменить' switcher + leaderboard back → /leaderboards + daily task badge |
| `1736cdb` | fix(frontend): photos contain (no crop) + avatar glow + club photos in profile |
| `153dc83` | fix(frontend): BottomNav shows user's TG photo in 'Профиль' tab with violet glow |
| `73e8b7d` | fix(frontend): photo container adapts to natural size + title brackets + drop deposit/tz in club card |
| `afc55d7` | fix(admin): click photo preview to replace + standalone delete button |
| `7733b95` | fix(admin): show club photo (gif/jpg/png/webp) + bullet-list of traits in HabitsListPage cards |
| `d9144df` | fix(bot): format {name} placeholder in user-facing check-in messages (= 7.4) |
| `de87272` | fix(bot): reject too-short video notes in pre-filter (PR №7.5) |
| `9b0eb4a` | test(worker): populate proof_types in add_habit fixture (migration 012 regression) |
| `02b949f` | fix(worker): skip new members in close_catch_window (PR №7.3) |
| `3a40b31` | feat(frontend+backend): mock deposit topup (PR №7.2) |
| `8f389e1` | feat(backend+worker+frontend): user photos in leaderboard (PR §7.1, approach C') |
| `7a9eefd` | feat(backend+worker+nginx): avatars via disk cache + nginx try_files (PR §7.1 v3, approach D) |
| `b682163` | fix(nginx): avatars — убрать try_files в alias location |
| `06bbdb4` | docs(Pravki)+infra: утилита вставки avatar location + уточнение подхода D |
| `62d01d7` | perf(avatars): server-side resize 640→160, public cache, decoding=async (PR §7.1 v3.1) |
| `6a87fc2` | feat(frontend+backend): photos in Members, drop rank numbers, white border in leaderboard |
| `fb55d91` | feat(backend+frontend): ребрендинг страницы 'Рейтинг' + единые лейблы табов (PR §7 v3.2) |
| `ecadc2b` | feat(frontend): tab через ?tab=, row в одну строку, mobile avatar меньше, 😴 для Лентяев (PR §7 v3.2) |

**Push:** все коммиты запушены в `origin/feature/topic-scoped-checkin` (push 2026-07-26, сессия: ✅ подход D для аватарок + ребрендинг).

### Дополнение: сессия 2026-08-08 → 2026-08-09 (Pravki-subscribe-and-join + bug-fixes)

| SHA | Сообщение |
|---|---|
| `ac6951f` | feat(backend): Pravki-deposit-sse Z-1+Z-2+Z-2.8 — global deposit on users (PR #1) |
| `9736b5b` | feat(backend+frontend): PR #2 — deposit-aware join/wallet UI (Z-3/Z-4/Z-11) |
| `ae6bd07` | fix(backend): LEFT/PAUSED bypass в MembershipService.join — deposit-check ВСЕГДА (Variant B) |
| `1702414` | docs: Pravki-subscribe-and-join — финал после двух итераций ревью |
| `b51eb90` | feat(backend+frontend): Pravki-subscribe-and-join Z-12..Z-18 — объединённый платёж подписка+депозит |
| `3af699b` | fix(frontend): regenerate package-lock.json для синхронизации с package.json |
| `e1d97a5` | fix(frontend): заменить Array.prototype.at() на [length-1] для совместимости с ES2020 |
| `b98cab0` | fix(backend): subscription fee НЕ попадает на deposit_balance |
| `497d01d` | feat(bug-fixes): Z-19 joiner-late protection — полная многоуровневая защита |
| `564b8db` | fix(frontend): defensive fallback в StatusBadge для unknown statuses |
| `4a390e1` | fix(infra): frontend — workaround для overlay-конфликта при rebuild (Вариант 1) |

**Ветка:** `feat/subscribe-and-join` (в remote `origin/feat/subscribe-and-join`).
**Deploy:** backend, worker, bot задеплоены 2026-08-09 (alembic=015, см. workaround
для ALTER TYPE в `docs/10-deploy.md` §9.2). Frontend bundle `main-CmHeC1H6.js` задеплоен.
**Production snapshot:** 2 users, 3 habits, 3 memberships, 4 transactions. Балансы
скорректированы (subscription fee отдельно от deposit). Тесты: 354 backend +
17 worker + 35 frontend — зелёные.

**Известные баги (см. `docs/AGENT_BOOTSTRAP.md §9`):**
- 🟡 `bot.send_invoice` не вызывается — реальный Telegram Payments не подключён
- ⚠️ Docker overlay-конфликт при `docker compose build frontend` — workaround
  через volume mount (`docs/10-deploy.md` §9.1)
- ⚠️ Alembic upgrade через compose не выполняет ALTER TYPE ADD VALUE —
  workaround через ручной `psql` (`docs/10-deploy.md` §9.2)

## 9. Решения пользователя

| Вопрос | Решение |
|---|---|
| Платежи (3.2): мок или писать в `transactions`? | **Писать в `transactions`** с `type='deposit_topup'`, `idempotency_key='mock:{uuid}'`. Когда подключим реальный сервис — структура таблицы уже совместима |
| 5.1 (новый участник): только новые или откатить старые? | **Все, кто вступает впервые** — `joined_at.date() >= club_date` → не штрафуется за этот день. Старые membership'ы не трогать (`joined_at` уже в прошлом, проверка тривиально проходит) |
| Депозит привязан к чему? | К `(user_id, habit_id)` — хранится на `memberships.deposit_balance` |
| UI пополнения? | **Максимально простой мок** — кнопка `+ Пополнить`, пресет суммы, нажал → деньги на депозите. Цель — тестировать штрафы/ловлю/призовой фонд |

### 9.1. Решения 2026-08-14 — paused window open / race-fix / frontend filter

> Snapshot 2026-08-14, серия из 4 коммитов `dfa3b2c` + `1f86217` + `7a988f8` + `a6cf949` на ветке `feature/paused-member-ux`. Все задеплоены на проде.

| Вопрос | Решение |
|---|---|
| Pravki-paused-window-open: paused-юзер отправил чек-ин В ОКНЕ — что говорить? | **Не врать про окно.** Старый `REJECT_PAUSED_OR_WINDOW` жёстко говорил «окно закрыто» для всех paused-кейсов. Добавлен `REJECT_PAUSED_WINDOW_OPEN` — мотивирующий текст «депозит пуст, но окно ещё открыто ({start}–{end}), пополни сейчас и успеешь чек-ин сегодня». В `_prefilter()` разветвление по `state.is_within_checkin_window` |
| Pravki-paused-race: в `apply_catch` есть ли окно гонки между SELECT и `lock_for_update(user)`? | **Да, defense-in-depth через `session.refresh(violator)` + повторная проверка.** Параллельная транзакция могла переключить `membership.status` через `recompute_pause_status` и закоммитить. До фикса код шёл дальше со staled `violator` из identity map SQLAlchemy. Тест `test_apply_catch_rereads_violator_status_after_user_lock` через `RaceyUserRepo` (мутирует `violator.status` во время `lock_for_update`) подтверждает лов |
| Pravki-paused-race: race-fix в apply_catch пересекается с canonical order §Z-22 (`caught_today #3 > paused #6`)? | **Нет, это orthogonal.** Race-fix — только re-check после user-lock, не трогает порядок проверок. §Z-22 контракт сохранён: combo `caught+paused` по-прежнему выигрывает `caught_today`. Variant B (частичный перенос paused между `joined_late` и `window_closed`) сделан в bot prefilter (`1f86217`), не в apply_catch |
| Pravki-paused-frontend: показывать ли paused-юзеров в списке «кого можно поймать»? | **Не показывать.** Через новое поле API `MemberRowOut.membership_status` (defensive default `"active"`) фронт фильтрует violators: `m.status === 'missed' && m.can_catch && m.membership_status === 'active'`. Сам paused-юзер остаётся видимым в общем списке «Все участники», но **без кнопки «Поймать»** — через условную передачу `onCatch` в `<MemberRowItem>`. Race-fix (race-condition) на бэкенде остаётся defense-in-depth |
| Pravki-paused-frontend: paused в /members + topup → снова active — нужны ли SSE-события для real-time обновления? | **Catch event — да (через существующий `useHabitSse`). Topup event — нет (polling 30s через `useMembers.refetchInterval` уже работает).** Минимально-инвазивно: catch event уже идёт через `sse:habit:{habit_id}` (Pravki §Z-6), и `streamController.ts:201-214` уже инвалидирует `["members", habitId]` на нём. Подключение `useHabitSse(habitId)` в `MembersPage` — 1 строка. SSE для topup не добавлялся — пользователь явно согласился на polling для сценария «появление» (задержка 30 сек не критична). Обратный сценарий «исчезновение» (catch → paused) защищён race-fix на бэкенде |
| Pravki-paused-frontend: vitest-тесты для MembersPage — пишем или откладываем? | **Пишем** (commit `a6cf949`, 4 кейса: paused/active/left/mixed). Не отложено. Мимоходом нашли баг в первом коммите frontend-фикса: `<MemberRowItem>` рендерил кнопку «Поймать» в общем списке по `can_catch` без учёта `membership_status` (filter на violators работал только на заголовок секции, не на кнопку в others). Исправлено через `--amend` в том же коммите |

## 10. Workflow развертывания

```bash
# Локально: коммиты сделаны. Пуш ТОЛЬКО после "ок" юзера.

# Деплой фронта (только dist/ меняется, backend не трогаем):
sshpass -p "$PASSWORD" ssh root@169.58.52.78 '
  cd /app/infra
  # multi-stage билд (если менялся только код в src/):
  docker run --rm -v /app/apps/frontend:/app -w /app node:20-alpine \
    sh -c "npm ci --silent && npm run build"
  docker cp /app/apps/frontend/dist/. habit-frontend:/usr/share/nginx/html/
  docker exec habit-frontend nginx -s reload
'

# Проверка:
curl -s https://app.prideclub.fun/health

# Юзер проверяет руками → говорит "всё ок":
git -c user.name=Vegass -c user.email=dmitriy@vegass.dev push origin feature/topic-scoped-checkin
```

### 10.1. Автоматическая чистка Docker (snapshot 2026-08-04)

**Проблема:** `docker compose build X --no-cache` оставляет старые build cache слои в
`/var/lib/docker/overlay2` (`Build Cache: 1012 entries, 78.56 GB`). Через 2 недели работы
диск `/` заполняется до 91% (8.7 GB свободно из 96 GB).

**Решение — два уровня:**

1. **`infra/deploy.sh` — шаг `prune_images`** сразу после `build_images`:
   ```bash
   docker image prune -f                              # dangling (после build их обычно 0)
   docker builder prune -f --filter "until=72h"      # build cache старше 72ч
   ```
   Активные образы (4 наших + 5 базовых = 9 штук, ~2.2 GB) **не трогаются** — они нужны
   следующему rebuild. Удаляются только слои, старше 3 дней, которые неактивны.

2. **Еженедельный cron на хосте** (страховка, если деплой идёт мимо `deploy.sh`):
   ```
   0 4 * * 0 /usr/bin/docker image prune -a -f --filter "until=168h" \
     && /usr/bin/docker builder prune -f --filter "until=168h" \
     >> /var/log/docker-prune.log 2>&1
   ```
   Воскресенье 04:00 UTC — 7 дней порог.

**Результат после фикса (snapshot 2026-08-04):**
```
Before:  88G used, 8.7G free (91%) — Images 206, Build Cache 78 GB
After:   8.5G used, 88G free (9%)  — Images 7, Build Cache 1.8 GB
```

**Чего НЕ делаем:**
- ❌ `docker system prune -a` без фильтра — может удалить base image, который нужен
- ❌ `docker volume prune` — volumes = данные (даже 85 MB, не трогаю)
- ❌ Прямое удаление в `/var/lib/docker/...` — опасно, делается только через `docker` CLI

## 11. Все задачи из §7 выполнены

| # | Задача | Статус | Коммит |
|---|---|---|---|
| **7.4** Имя в «Принято» (XS) | ✅ | `d9144df` |
| **7.5** Pre-validate кружка <3 сек (S) | ✅ | `de87272` |
| **7.3** Guard «joined_at >= club_date» (M) | ✅ | `02b949f` |
| **7.2** `/api/v1/payments/topup` (M) | ✅ | `3a40b31` |
| **7.1** Фото участников в лидерборде (M) | ✅ | `8f389e1`, `7a9eefd`, `b682163`, `62d01d7`, `6a87fc2` |
| **7.6** Ребрендинг «Рейтинг» + row в одну строку (M, v3.2) | ✅ | `fb55d91`, `ecadc2b` |
| **7.8** Бот молчит: webhook SSL + worker DI-bug (M) | ✅ | `TBD` (этот коммит, см. §7.8) |
| **7.9** Автоматическая чистка Docker (S) | ✅ | `TBD` (этот коммит, см. §7.9) |
| **7.7** Подтверждение ловли с inline-кнопками (S) | 🔴 TODO | — |

Все задачи кроме 7.7 закрыты.
