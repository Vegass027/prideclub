# Frontend Status — что сделано и что работает

> Telegram Mini App для Habit Club (PrideClub). React 18 + TypeScript + Vite + Tailwind + React Query + Zustand.
> **Production**: `https://app.prideclub.fun/`

---

## Стек

| Слой | Технология |
|------|------------|
| Framework | React 18, TypeScript (strict) |
| Build | Vite 5 |
| Стили | TailwindCSS 3, кастомная палитра (canvas / card / accent) |
| State (server) | TanStack Query v5 |
| State (UI) | Zustand |
| Routing | React Router v6 |
| Telegram | `@telegram-apps/sdk` через `window.Telegram.WebApp` |

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
│   ├── api/          # fetch-клиент + типизированные endpoint'ы
│   ├── hooks/        # useQuery / useMutation обёртки
│   ├── telegram/     # TMA bootstrap, getUser, getUserPhoto
│   ├── ui/           # BottomNav, HabitNav, Avatar, Modal'ы, ...
│   └── types/        # API DTO
│
└── index.css         # Tailwind directives + safe-area переменные
```

---

## Маршруты

| Path | Страница | Назначение |
|------|----------|------------|
| `/onboarding` | OnboardingPage | Редирект: 1 клуб → `/habits/:id/today`, иначе → `/my-habits` |
| `/marketplace` | MarketplacePage | Каталог клубов, вступление через PaymentModal |
| `/my-habits` | MyHabitsPage | Список клубов, в которых состоит юзер |
| `/habits/:id/today` | TodayPage | Статус чек-ина на сегодня |
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
- **Вступить** → `PaymentModal` (мок, 3 шага: review → processing → success) → `POST /habits/:id/join` → переход в Today.
- **Уже состоит** → кнопка «Открыть клуб →».
- Кнопка «Подробнее» раскрывает описание.

### ✅ MyHabits
- Список клубов юзера из `GET /me/habits`.
- Карточка: title + description + статус активности.
- «Открыть клуб →» → Today.
- Пусто: CTA «Выбрать клуб» → Marketplace.

### ✅ Today (внутри клуба)
- Hero-карточка с описанием привычки + окно чек-ина.
- `StatusBadge`: ожидает / принят / пропущен / не в окне.
- Кнопки: «Сменить клуб» (если клубов >1), «Открыть лидерборд».
- Back-кнопка `PageHeader.backTo` → `/profile` (если клубов ≤1) или `/my-habits`.

### ✅ Members (внутри клуба)
- Список участников с Avatar (md).
- Streak-счётчик и поимки у каждого.
- Кнопка «Спалить» → `POST /catch`.
- Back → `/profile` или `/my-habits`.

### ✅ Leaderboard (внутри клуба)
- 3 вкладки: 🔥 Серии / 🎯 Ловцы / 💀 Позор.
- Полный список участников с медалями (🥇🥈🥉).
- Back → `/profile` или `/my-habits`.

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
  - Кнопка «+ Пополнить» → `TopUpModal` (4 пресета 299/599/999/1999 ₽).
- **Мои клубы**: карточки с описанием и кнопкой «Открыть клуб →».
- **Все клубы →**: secondary кнопка → Marketplace.
- Всегда отображается **BottomNav** (не HabitNav) — глобальный контекст.

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
| `PaymentModal` | bottom-sheet для оплаты (мок) |
| `TopUpModal` | bottom-sheet для пополнения (мок) |

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
| Реальная интеграция ЮKassa / Telegram Stars | ⏳ мок PaymentModal |
| Реальное пополнение депозита | ⏳ мок TopUpModal |
| Загрузка чекин-медиа (фото/video_note) | ❌ только статус, без UI |
| Push-уведомления через бота | ❌ |
| Локализация (i18n) | ❌ только ru-RU |
| Dark/Light theme switch | ❌ только dark |
| Onboarding tutorial | ❌ |
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

# Деплой на сервер
rsync -az apps/frontend/src/ root@169.58.52.78:/app/apps/frontend/src/
rsync -az apps/frontend/dist/ root@169.58.52.78:/app/apps/frontend/dist/
ssh root@169.58.52.78 'cd /app/infra && docker compose build frontend --no-cache && docker compose up -d frontend'
```

CI:
- `npm run build` в `.github/workflows/frontend-ci.yml` (если есть).
- TypeScript strict — `npx tsc --noEmit` проходит.

---

## Метрики

- **Bundle**: ~346 KB JS (gzip ~110 KB), 14 KB CSS (gzip ~4 KB).
- **Страниц**: 9.
- **Компонентов UI**: 13.
- **Хуков**: 11.
- **TS strict**: ✅.
- **Lint (eslint)**: ✅ (если настроен).
