# Frontend Status — что сделано и что работает

> Snapshot от 2026-07-23. Включает **topic-scoped чек-ины** (миграции 010/011,
> ветка `feature/topic-scoped-checkin`), **третий топик** для чата клуба,
> кнопки «🎬 Сделать чек-ин» / «💬 Перейти в чат» / «👋 Присоединиться к клубу»
> и тёмный фон Mini App. Платежи = мок на фронте (`PaymentModal.setTimeout`,
> `TopUpModal.alert`), бот не вызывает `bot.send_invoice`. См.
> [09-prod-readiness.md](../../../docs/09-prod-readiness.md).

> Telegram Mini App для Habit Club (PrideClub). React 18 + TypeScript + Vite + Tailwind + React Query + Zustand.
> **Production**:
> - User Mini App: `https://app.prideclub.fun/`
> - Admin Mini App: `https://admin.prideclub.fun/` (owner-only, через `OWNER_TELEGRAM_ID`)

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
- Hero-карточка с описанием привычки + окно чек-ина.
- `StatusBadge`: ожидает / принят / пропущен / не в окне.
- **Топик-фильтр чек-инов (миграция 010)**: кнопка «🎬 Сделать чек-ин» появляется
  если у клуба задан `checkin_topic_thread_id`. Открывает топик чек-инов через
  `openCheckinTopic(chat_id, thread_id)`. URL формируется как
  `https://t.me/c/<short_chat_id>/<thread_id>` (без префикса `-100`).
- **Третий топик (миграция 011)**: кнопка «💬 Перейти в чат» появляется если
  задан `chat_topic_thread_id`. Открывает топик общего чата участников.
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
rsync -az apps/frontend/ root@169.58.52.78:/app/apps/frontend/
ssh root@169.58.52.78 'cd /app/infra && docker compose build frontend --no-cache && docker compose up -d frontend'
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
