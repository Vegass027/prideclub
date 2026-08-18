# Frontend Status — что сделано и что работает

> Snapshot от 2026-07-23 (обновлено 2026-08-07 после Step 7 — успешный деплой
> SSE+Redis Streams). Включает **topic-scoped чек-ины** (миграции 010/011,
> ветка `feature/topic-scoped-checkin`), **третий топик** для чата клуба,
> кнопки «🎬 Сделать чек-ин» / «💬 Перейти в чат» / «👋 Присоединиться к клубу»
> и тёмный фон Mini App. **Real-time updates через SSE** (commits `5d8c6e6`+`d30832a`):
> `useTodayStream` хук с manual reconnect-loop, `streamController` pure-function
> с DI, `sseToken` API — см. раздел «Real-time updates (SSE)». Платежи = мок на фронте
> (`PaymentModal.setTimeout`, `TopUpModal.alert`), бот не вызывает `bot.send_invoice`. См.
> [09-prod-readiness.md](../../../docs/09-prod-readiness.md).

> Telegram Mini App для Habit Club (PrideClub). React 18 + TypeScript + Vite + Tailwind + React Query + Zustand.
> **Production**:
> - User Mini App: `https://app.prideclub.fun/`
> - Admin Mini App: `https://admin.prideclub.fun/` (owner-only, через `OWNER_TELEGRAM_ID`)
>
> **Snapshot 2026-08-18 (Pravki-subscription-2026-08-17 deploy на HEAD `403219d`; ранее 2026-08-09 Pravki-subscribe-and-join + bug-fixes Z-19):**
> bundle `main-BY8E9p_-.js` + `index-D1YDiVX1.js` + `admin-CoTYu9ZG.js`
> (включает `JoinPayModal` с режимом `renew-only` для smart renew,
> 3-state `SubscriptionBadge` для бейджа подписки 1-2 дня / expired,
> блок `joined_late` в `TodayPage`, defensive fallback в `StatusBadge`).
> Compose workaround для overlay-конфликта — `image: nginx:1.27-alpine` +
> volume mount на bundle, см. `docs/10-deploy.md` §9.1.
> **ВНИМАНИЕ:** не возвращать `build:` в compose до диагностики
> overlay-конфликта (см. отдельную задачу в репо).

---

## Стек

| Слой | Технология |
|------|------------|
| Framework | React 18, TypeScript (strict) |
| Build | Vite **6** (multi-stage Docker: node:20-alpine → nginx:1.27-alpine) |
| Стили | TailwindCSS 3, кастомная палитра (canvas / card / accent) |
| State (server) | TanStack Query **v5** |
| State (UI) | Zustand **v5** |
| Routing | React Router **v6** |
| Telegram | `@telegram-apps/sdk` **3.3** через `window.Telegram.WebApp` |

---

## Структура проекта

```
apps/frontend/src/
├── app/              # Bootstrap, router, providers
│   ├── App.tsx       # initTelegram() + QueryClient + Router
│   └── router.tsx    # все маршруты
│
├── pages/            # 1 директория = 1 экран
│   ├── Onboarding/
│   ├── Marketplace/
│   ├── MyHabits/
│   ├── Today/
│   ├── Members/
│   ├── Leaderboard/      # внутри клуба (3 вкладки)
│   ├── GlobalLeaderboard/ # рейтинг по всем клубам юзера
│   └── Profile/
│
├── shared/
│   ├── api/          # axios-клиент + типизированные endpoint'ы
│   ├── hooks/        # useQuery / useMutation обёртки
│   ├── telegram/     # TMA bootstrap, getUser, getUserPhoto (через @telegram-apps/sdk 3.3)
│   ├── ui/           # BottomNav, HabitNav, Avatar, Modal'ы, ...
│   ├── types/        # API DTO
│   └── utils/        # formatKopecks и др.
│
├── admin/            # Admin Mini App (отдельный роутер, отдельный nginx endpoint)
│   ├── api/          # adminHabitsApi (CRUD, activate, archive, restore)
│   ├── components/   # AdminHabitCard
│   ├── hooks.ts      # useAdminHabits, useActivateHabit, useDeleteHabit, useRestoreHabit, usePermanentDeleteHabit
│   └── pages/        # HabitsListPage (с фильтрами Активные/Скрытые/Архив), HabitCreatePage, HabitEditForm
│
└── index.css         # Tailwind directives + safe-area переменные
```

---

## Маршруты

| Path | Страница | Назначение |
|------|----------|------------|
| `/onboarding` | OnboardingPage | Редирект: 1 клуб → `/habits/:id/today`, иначе → `/profile` |
| `/marketplace` | MarketplacePage | Каталог клубов, вступление через **мок-PaymentModal** |
| `/my-habits` | (редирект) | Старый URL → `/profile`. Удалено как дубликат «Моих клубов» в `/profile` |
| `/habits/:id/today` | TodayPage | Статус чек-ина на сегодня + секция «Клуб в Telegram» |
| `/habits/:id/members` | MembersPage | Участники + кнопка «спалить» |
| `/habits/:id/leaderboard/:tab` | LeaderboardPage | Лидерборд клуба (streak/catches/shame) |
| `/leaderboards` | GlobalLeaderboardPage | Группированный по клубам рейтинг, топ-3 в каждом |
| `/profile` | ProfilePage | Аватар + депозит + мои клубы + история |

Все переходы — `<Navigate>` / `useNavigate()` (без `history.back()`, чтобы не терять контекст).

---

## Что работает

### ✅ Onboarding / Bootstrap
- `initTelegram()`: если Mini App открыт в Telegram — берёт `initDataUnsafe.user`, ставит `ready()`, expand, theme params.
- Если открыт вне Telegram (dev) — мок-юзер, mock initData для API.
- QueryClient с `staleTime: 30s`, `refetchOnWindowFocus: false`.

### ✅ Marketplace
- Список клубов из `GET /marketplace`.
- Карточка: title, description, окно чек-ина, штраф, подписка, фонд, кол-во участников.
- **Вступить** → `PaymentModal` (**мок**, 3 шага: review → processing → success через
  `setTimeout(1200)`) → `POST /habits/:id/join` → переход в Today. В моде текст
  явно: *"Сейчас платёжный шлюз не подключён"*. Реальный платёжный провайдер
  не подключён.
- **Уже состоит** → кнопка «Открыть клуб →».
- Кнопка «Подробнее» раскрывает описание.

### ✅ MyHabits
- Удалена как отдельная страница 2026-07-23 (дублировала «Мои клубы» в `/profile`).
- Старый URL `/my-habits` редиректит на `/profile`.
- «Мои клубы» теперь живут только на странице профиля.

### ✅ Today (внутри клуба)

**Обновлено 2026-08-09 (Pravki-subscribe-and-join + bug-fixes Z-19):**
- `JoinPayModal` — модалка оплаты при первом вступлении (чекбокс подписки +
  пресеты депозита из `topupPresets.ts`, отфильтрованные по `penalty_amount`,
  кнопка «Оплатить X ₽» с total = `price_month + deposit`).
- `JoinButton` — открывает `JoinPayModal` (вместо прямого POST `/join` как в PR #2).
- Блок для статуса `joined_late` (новый Pravki-bug-fixes Z-19): «Вы вступили
  после чек-ина. Следующая отметка — завтра.» Нейтральный тон (не штрафной).
- Defensive fallback в `StatusBadge` для рассинхрона кэша браузера:
  `statusConfig[status] ?? FALLBACK_BADGE` — unknown статус показывает `•`,
  не падает (commit `564b8db`).
- Hero-карточка с описанием привычки + окно чек-ина.
- `StatusBadge`: ожидает / принят / пропущен / не в окне.
- **Топик-фильтр чек-инов (миграция 010)**: кнопка «🎬 Сделать чек-ин» появляется
  если у клуба задан `checkin_topic_thread_id`. Открывает топик чек-инов через
  `openCheckinTopic(chat_id, thread_id)`. URL формируется как
  `https://t.me/c/<short_chat_id>/<thread_id>` (без префикса `-100`).
- **Третий топик (миграция 011)**: кнопка «💬 Перейти в чат» появляется если
  задан `chat_topic_thread_id`. Открывает топик общего чата участников.
- **Multi-proof_types (PR №8)**: карточка «Чек-ин» показывает все разрешённые типы
  подтверждения, которые админ задал в клубе (`proof_types: ProofType[]`):
  - 1 тип — старая карточка «🎥 Видео-кружочек» / «📸 Фото» / «✍️ Текст».
  - 2+ типа — заголовок «🎯 Чек-ин — любой из типов» + список всех типов
    с эмодзи и подсказками.
  - Fallback `resolveProofTypes()`: если бэкенд почему-то не вернул `proof_types`,
    использует `[proof_type]` для обратной совместимости.
- **Секция «Клуб в Telegram»**:
  - Если `membership.status === "active"` → disabled-кнопка «❤️ Вы состоите в клубе»
    + «💬 Открыть группу» (если есть `telegram_invite_link`).
  - Иначе → «👋 Присоединиться к клубу» (открывает `telegram_invite_link` или
    корневую ссылку на группу).
- Back-кнопка `PageHeader.backTo` → `/profile`.

### ✅ Members (внутри клуба)
- Список участников с Avatar (md).
- Streak-счётчик и поимки у каждого.
- Кнопка «Спалить» → `POST /catch`.
- Back → `/profile`.

### ✅ Leaderboard (внутри клуба)
- 3 вкладки: 🔥 Серии / 🎯 Ловцы / 💀 Позор.
- Полный список участников с медалями (🥇🥈🥉).
- Back → `/profile`.

### ✅ Global Leaderboard (рейтинг)
- 3 вкладки: 🔥 Серии / 🎯 Ловцы / 💀 Позор.
- **Группировка по клубам юзера** (не сквозная таблица).
- В каждом клубе: title + кол-во участников + **топ-3** с медалями + кнопка «Открыть клуб →».
- Endpoint: `GET /leaderboard/{tab}/overview` → `{ tab, metric_label, clubs: [{ habit_id, title, members_count, top: [...] }] }`.
- Empty state если нет клубов / нет активности.

### ✅ Profile
- **Avatar** (lg): `<img src={photo_url} onError → fallback на инициалы>`.
- **Депозит**:
  - Буллеты: «Депозит покрывает штрафы в клубах», «Если депозит пуст — ты выбываешь из клуба ☹️».
  - Сумма + история транзакций (последние N).
  - Кнопка «+ Пополнить» → `TopUpModal` (**мок**, 4 пресета 299/599/999/1999 ₽,
    `alert("Пополнение на N ₽ скоро будет доступно")`).
- **Мои клубы**: карточки с описанием и кнопкой «Открыть клуб →».
- **Все клубы →**: secondary кнопка → Marketplace.
- Всегда отображается **BottomNav** (не HabitNav) — глобальный контекст.
- **AI-комендант и "Удалить аккаунт"** — **отсутствуют** в MVP (запланировано в v2).

### ✅ Admin Mini App (`src/admin/`)

- **Хост:** `https://admin.prideclub.fun/` (отдельный nginx endpoint, отдельный `admin.html`).
- **Owner-gate:** через `OWNER_TELEGRAM_ID` в `core/middleware.py`.
- **Функционал:**
  - `HabitsListPage` — список клубов с фильтрами **Активные / Скрытые / Архив** (фильтр "Все" удалён).
  - `HabitCreatePage` — форма создания клуба. Поля **обязательны**:
    ссылки на топики чек-инов, уведомлений и чата клуба
    (`https://t.me/c/<chat_id>/<thread_id>`). Также: `telegram_invite_link`,
    `chat_id`, `photo upload`, окно чек-ина, цена, штраф, timezone, proof_type.
  - `HabitEditForm` — редактирование существующего клуба, ссылки на топики
    опциональны (но все три должны различаться и быть в одной группе).
  - `AdminHabitCard` — карточка с toggle is_active, кнопками **delete / restore / permanent delete**.
  - `uploads.ts` API — загрузка фото (`POST /admin/v1/uploads`), файл попадает в volume `club_uploads` и отдаётся nginx'ом.

### ✅ Bottom Navigation
- `fixed bottom-0 inset-x-0`, `pb-[env(safe-area-inset-bottom)]`, `bg-canvas/95 backdrop-blur`, `z-40`.
- 3 кнопки:
  - 🏪 **Клубы** → `/marketplace`
  - 🏆 **Рейтинг** → `/leaderboards`
  - 👤 **Профиль** → `/profile`

### ✅ Habit Navigation (внутри клуба)
- `fixed bottom-0`, 3 кнопки:
  - 📅 **Сегодня** → `/habits/:id/today`
  - 👥 **Участники** → `/habits/:id/members`
  - 🏆 **Лидеры** → `/habits/:id/leaderboard/streak`

### ✅ Telegram WebApp
- `initTelegram()` вызывается в `App.tsx` ДО монтирования роутера.
- `Telegram.WebApp.ready()`, `.expand()`, `.setHeaderColor()`, `.setBackgroundColor()`.
- API-клиент автоматически добавляет `X-Telegram-Init-Data` в каждый запрос.
- `getUser()` / `getUserPhoto()` — typed доступ к `initDataUnsafe.user`.

### ✅ Real-time updates (SSE) — Step 6 (2026-08-04)

**Backend pipeline** (Steps 1-4, `feature/topic-scoped-checkin` → main):
- `POST /api/v1/events/stream/token` — выдаёт JWT (HS256, TTL 60 с) после initData-auth
  + membership-check. Подписан `SSE_TOKEN_SECRET` (отдельный от `SERVICE_SECRET`).
- `GET /api/v1/events/stream?habit_id=…&token=…` — `StreamingResponse(text/event-stream)`
  с `XREAD BLOCK 30000 STREAMS sse:user:{u}:{h} <start_id>` через `redis_stream_bus.py`.
  `: heartbeat\n\n` на пустой XREAD. Async-Redis singleton (`db/redis_async.py`)
  — один пул на процесс.
- Worker `event_publisher.py` — `SET sse_published:checkin:{m}:{d} NX EX 86400`
  перед `XADD sse:user:{u}:{h} MAXLEN ~ 1000 * event=checkin.accepted|rejected habit_id ... payload {...}`.
  Guard 1 (early-skip на дубль) + Guard 2 (idempotency через SET NX).
- nginx exact-match `location = /api/v1/events/stream` (Step 5, commit `900ef4f`):
  `proxy_buffering off`, `proxy_read_timeout 3600s`, `proxy_send_timeout 3600s`,
  `access_log off` (SSE-токен в query, не логируется).

**Frontend files** (commits `5d8c6e6` + `d30832a`):
- `apps/frontend/src/shared/api/sseToken.ts` — `sseTokenApi.request(habitId)` через axios
  (initData rides on interceptor).
- `apps/frontend/src/shared/hooks/streamController.ts` — **pure-function controller**
  с DI через 7 параметров (`habitId, queryClient, createEventSource, requestToken,
  setTimeoutFn, clearTimeoutFn, onError, streamBaseUrl`). Manual reconnect-loop с
  backoff `[1s, 2s, 5s, 10s]` cap, inFlight race-protection. НЕ полагается на
  нативный EventSource auto-reconnect — он ре-шлёт протухший токен, EventSource
  при 401 закрывается насовсем, в Telegram WebView сеть рвётся регулярно.
- `apps/frontend/src/shared/hooks/useTodayStream.ts` — тонкая обёртка
  (`useEffect` + `useRef`), ответственная только за lifecycle. Cleanup в
  `useEffect` закрывает EventSource и отменяет pending backoff-таймер.
- `apps/frontend/src/pages/Today/TodayPage.tsx` — `useTodayStream(habitId)`
  подключён. **Mount-invalidate `useEffect(invalidateQueries)` удалён** (`d30832a`)
  как избыточный — на масштабе 1000+ юзеров это конкретная лишняя нагрузка
  на backend без реальной пользы. `useToday` через `useQuery` со `staleTime: 30_000`
  сам управляет stale-инвалидацией.

**Тесты** (`apps/frontend/src/shared/hooks/__tests__/streamController.test.ts`):
11 vitest unit. Покрытие: initial open URL shape, `checkin.accepted` →
`setQueryData` с распарсенным payload + `lastEventId` persistence, `checkin.rejected`
→ `onError` с message из payload, onerror → close + backoff + новый EventSource
с свежим токеном (НЕ с тем же — иначе нативный EventSource сдохнет через TTL=60с),
backoff cap на 10 с, `lastEventId` в reconnect URL, `stop()` отменяет pending
backoff, `requestToken throws` → backoff retry, `start()` идемпотентность.

**Архитектурный выбор — pure-function controller (не inline в хуке):**
выделен из хука для тестируемости без `@testing-library/react` (его нет в
`package.json`). DI через 7 параметров позволяет мокать EventSource,
queryClient, requestToken, setTimeout, onError. Не создаёт React-зависимости
в контроллере — следует той же дисциплине что и backend (DI через конструктор,
никаких глобальных состояний). **One `as unknown as` каст в одной DI-точке**
(`EventSource` → `StreamEventSourceCtor`) — TypeScript не делает covariance на
constructor return types, каст явно помечен комментарием с обоснованием.

**Telegram WebView совместимость:** EventSource не поддерживает кастомные
заголовки (нет initData), поэтому SSE-контур использует двухступенчатый flow —
`POST /events/stream/token` с initData → JWT → `GET /events/stream?token=…`.
Nginx exact-match блок работает только для GET на `/api/v1/events/stream`,
POST `/events/stream/token` остаётся под общим `/api/` блоком с initData-middleware.

**Деплой:** используется метод из `docs/02-architecture.md §13` —
`docker run node:20-alpine + docker cp dist + nginx -s reload`. НЕ `docker
compose build frontend` (двухслойный nginx не обновит dist в работающем контейнере).

---

### ✅ checkin.rejected mapper — Step 5 (Pravki §Z-22, 2026-08-12)

**Проблема (закрытая):** через SSE `checkin.rejected` приходил сырой
`payload.message` (например, `checkin_window_closed`), который фронт
показывал в `Telegram.WebApp.showAlert()` без перевода. Юзер видел машинный
код в alert'е. Симметрично бою (`apps/bot/bot/handlers/checkin_texts.py`)
фронт тоже должен иметь mapper.

**Решение** (commit `b4cc923`):
- `apps/frontend/src/shared/texts/checkinReject.ts` (NEW) —
  `checkinRejectText(code, ctx)` симметричный `bot._text_for_code`.
  14 кодов покрыты (все из `CheckinRejectCode` enum, кроме legacy
  `MEMBERSHIP_NOT_ACTIVE` — fallback на `REJECT_UNKNOWN`).
- `apps/frontend/src/shared/hooks/streamController.ts:152-178` —
  `checkin.rejected` handler теперь вызывает `checkinRejectText` вместо
  pass-through `payload.message`. Битый JSON или unknown code →
  `REJECT_UNKNOWN`.
- `apps/frontend/src/shared/types/checkinReject.ts` (уже из Шага 0) —
  TS mirror enum, single source of truth с backend.

**Источник истины:**
- Backend: `apps/backend/app/core/constants.py:CheckinRejectCode` (enum)
- Frontend: `apps/frontend/src/shared/types/checkinReject.ts` (TS mirror)
- Тесты drift: `apps/backend/tests/test_checkin_reject_codes.py` +
  `apps/frontend/src/shared/types/__tests__/checkinReject.test.ts`

**Известное ограничение** (НЕ блокирует): `caught_today` vs `missed`
различие для фронта отсутствует — worker SSE payload `{reason, message}`
не содержит `checkin_status`. Mapper использует общий текст «поймали»
на оба случая (финансово одинаковый результат, см. `docs/09-prod-readiness.md §3`).
Бот различает их через `state.checkin_status` в `HabitStateResponse`.
Расширение `_publish_checkin_rejected` отдельным PR.

**Тесты** (`apps/frontend/src/shared/texts/__tests__/checkinReject.test.ts`):
16 vitest unit + 4 новых в `streamController.test.ts` (mapped text вместо
pass-through). Общий test baseline: 68 frontend тестов (было 50, +18).

---

## UI Kit (`shared/ui/`)

| Компонент | Назначение |
|-----------|-----------|
| `Button` | primary / secondary / ghost / danger |
| `BottomNav` | фикс-нав для глобальных экранов |
| `HabitNav` | фикс-нав внутри клуба |
| `ScreenLayout` | `mx-auto max-w-md px-4 pb-24 pt-4` |
| `PageHeader` | title + subtitle + back (с `backTo`) |
| `Avatar` | img + onError → инициалы (sm/md/lg) |
| `Tabs` | 3 вкладки лидерборда |
| `StatusBadge` / `StatusDot` | статус чек-ина |
| `EmptyState` | пустое состояние |
| `Skeleton` | loading placeholder |
| `PaymentModal` | bottom-sheet для оплаты (**мок**, `setTimeout(1200)`) |
| `TopUpModal` | bottom-sheet для пополнения (**мок**, `alert()`) |

---

## API клиент

`shared/api/client.ts` — fetch-обёртка:
- Базовый URL: `https://app.prideclub.fun/api/v1` (или `/api/v1` через nginx).
- Авто-инжект `X-Telegram-Init-Data` из localStorage / Telegram.
- Парсинг `initDataUnsafe` для dev-режима.
- `ApiError` класс с status + message.

`shared/api/index.ts` — типизированные endpoint'ы:
- `marketplaceApi.list()`
- `habitsApi.today(id)`, `habitsApi.mine()`, `habitsApi.join(id)`, `habitsApi.leave(id)`
- `membersApi.list(id)`, `membersApi.catch(memberId)`
- `leaderboardApi.global(tab)`, `leaderboardApi.overview(tab)`, `leaderboardApi.club(habitId, tab)`
- `balanceApi.get()`

---

## Hooks

Все используют React Query (TanStack Query v5):
- `useMarketplace`, `useToday(id)`, `useJoinHabit`, `useLeaveHabit`
- `useMembers(id)`, `useCatch`
- `useLeaderboard(tab, habitId?)`, `useGlobalLeaderboard`, `useLeaderboardOverview`
- `useMyHabits`, `useBalance`
- `useUser()` — текущий юзер из Telegram

---

## Антифрод и UX-детали

- **Не в Telegram** → мок initData, мок-юзер, баннер «dev mode».
- **Loading state** — Skeleton / spinner.
- **Error state** — `EmptyState` с `error.message`.
- **Empty state** — отдельный компонент, CTA.
- **Все мутации** — `isPending`, `onError`, toast.
- **Back-кнопка** всегда ведёт в логичное место (не `history.back()`).

---

## Что НЕ сделано / TODO

| Что | Статус |
|-----|--------|
| Реальная интеграция ЮKassa / Telegram Stars | ❌ мок PaymentModal (бэк/bot код подготовлен, но бот не вызывает send_invoice) |
| Реальное пополнение депозита | ❌ мок TopUpModal (`alert()`) |
| Загрузка чекин-медиа (фото/video_note) на клиенте | ❌ только статус, без UI загрузки |
| Push-уведомления через бота | ❌ |
| Локализация (i18n) | ❌ только ru-RU |
| Dark/Light theme switch | ❌ только dark |
| Onboarding tutorial | ❌ редирект-страница только |
| AI-комендант / "Удалить мои данные" | ❌ в v2 |
| A11y audit | ⏳ базовая (aria-labels, tabindex) |
| E2E тесты (Playwright) | ❌ |
| Unit-тесты (Vitest) | ❌ |

---

## Билд и деплой

```bash
# Локально
cd apps/frontend
npm install
npm run build       # → dist/

# Деплой на сервер — только src/, dist/ собирается внутри Docker:
rsync -az apps/frontend/ privichki-prod:/app/apps/frontend/
ssh privichki-prod 'cd /app/infra && docker compose build frontend --no-cache && docker compose up -d frontend'
```

> ⚠️ `dist/` на хосте (`/app/apps/frontend/dist/`) — артефакт локальных сборок,
> **не** используется контейнером. Контейнер собирает свой `dist` внутри multi-stage
> `node:20-alpine` → `nginx:1.27-alpine`. Не путать при диагностике.

CI:
- `npm run build` в `.github/workflows/frontend-ci.yml`.
- TypeScript strict — `npx tsc --noEmit` проходит.

---

## Метрики (последний локальный билд, 2026-07-23)

- **Bundle**: `index-*.js` ~310 KB (gzip ~102 KB), `admin-*.js` ~47 KB (gzip ~12 KB),
  `main-*.js` ~40 KB (gzip ~11 KB), `index-*.css` ~18 KB (gzip ~5 KB).
- **Страниц user Mini App**: 7 (Onboarding, Marketplace, Today, Members, Leaderboard, GlobalLeaderboard, Profile). `MyHabitsPage` удалена 2026-07-23.
- **Страниц Admin Mini App**: 3 (HabitsListPage, HabitCreatePage, HabitEditForm).
- **Компонентов UI**: ~13 (`shared/ui/`).
- **Хуков**: ~11 (user) + 5 admin-хуков.
- **TS strict**: ✅.
- **Lint (eslint)**: ✅.

### Изменения этой итерации (2026-07-23)

- **Topic-scoped чек-ины**: миграции 010, 011 → топики чек-инов, уведомлений, чата клуба.
  Бот фильтрует по `message_thread_id`. Кнопки «🎬 Сделать чек-ин» / «💬 Перейти в чат»
  на `TodayPage` и в карточках клубов.
- **Секция «Клуб в Telegram»**: показывает состояние членства («❤️ Вы состоите в клубе»)
  или CTA «👋 Присоединиться к клубу».
- **Удалена страница `/my-habits`** — дублировала «Мои клубы» в `/profile`.
- **Тёмный фон Mini App**: новый файл `telegram-bg.ts` с side-effect импортом,
  фиксирует `setHeaderColor/setBackgroundColor/setBottomBarColor("#0F1115")`
  на загрузке, чтобы фон не моргал белым.
- **Поле `telegram_invite_link` в Admin Mini App** — обязательное для Invite-кнопки.
- **Парсер ссылок Telegram** (`core/telegram_links.py`): нормализует короткий `chat_id`
  в Bot API-форму (`-100<short_id>`).
- **Multi-proof_types в карточке «Чек-ин» (PR №8)**: бэкенд отдаёт
  `habit.proof_types: ProofType[]` (после миграции 012). Карточка «Чек-ин» на
  `TodayPage` показывает все разрешённые типы подтверждения:
  - 1 тип → как раньше (одна эмодзи + подсказка).
  - 2-3 типа → «🎯 Чек-ин — любой из типов» + список с эмодзи и подсказками
    для каждого. Помогает участникам понять, что можно отправить не только
    кружок, но и фото/текст, если клуб это разрешает.
  - Резолвер `resolveProofTypes()` в `TodayPage.tsx` fallback'ит на `[proof_type]`,
    если бэкенд почему-то не вернул `proof_types` (обратная совместимость).
  - **Backend fix**: `apps/backend/app/api/v1/habits.py` — в `HabitOut(...)`
    во всех трёх эндпоинтах (`/marketplace`, `/habits/:id/today`, `/habits/my`)
    добавлено `proof_types=list(h.proof_types)`. Без этого фронт получал `[]`
    и fallback'ил на один тип — фича не работала визуально.
  - **Тип `Habit.proof_types: ProofType[]`** добавлен в
    `apps/frontend/src/shared/types/index.ts`.
- **Bot pre-filter (PR №9)**: бот проверяет `allowed_proof_types` и
  `already_checked_in` ДО отправки в backend через новый
  `GET /internal/bot/habit_state?chat_id=...&user_id=...`. Юзер сразу
  получает понятное сообщение вместо ложного «Принято, молодец» (когда
  worker асинхронно отвергает задачу с `code: wrong_type` или
  `checkin_already_exists`). Сообщения:
  - Неподдерживаемый тип в клубе с одним типом: «В этом клубе принимается
    только 🎥 Видео-кружочек. Отправь 🎥 видео-кружочек».
  - Неподдерживаемый тип в клубе с 2-3 типами: «🎯 Этот клуб принимает
    только: 🎥 Видео-кружочек, 📸 Фото. Отправь любой из этих типов».
  - Уже отмечен сегодня: «Ты уже отметился сегодня. Повторно не нужно —
    молодец 😉».
  - **Backend**: `apps/backend/app/api/v1/internal_bot.py` — новый endpoint
    `HabitStateResponse(found, habit_id, proof_types, checkin_topic_thread_id,
    already_checked_in, checked_in_at)`. 7 unit-тестов в
    `tests/test_internal_habit_state.py`.
  - **Bot**: `apps/bot/bot/handlers/checkin.py` — новый `_prefilter()`
    вызывается до `backend.post()`. `BackendClient.get_habit_state(chat_id,
    user_id)` для запроса. 6 новых тестов в `tests/test_checkin_handler.py`
    (wrong_type_single, wrong_type_multi, already_checked_in,
    habit_not_found_silent, state_error_fallback, correct_type_proceeds).
  - **Поведение на проде проверено**: после деплоя юзер отправил фото в
    клуб с `proof_types=["video_note"]` → бот ответил «в этом клубе
    принимается только 🎥 Видео-кружочек» вместо ложного «Принято».
