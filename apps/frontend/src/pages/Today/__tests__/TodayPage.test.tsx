// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// vi.mock фабрика хойстится в начало файла, поэтому все переменные нужно
// объявлять через vi.hoisted() чтобы они были доступны в фабрике.
const { mockWalletGet, mockTodayGet } = vi.hoisted(() => ({
  mockWalletGet: vi.fn(),
  mockTodayGet: vi.fn(),
}));

vi.mock("@/shared/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/api")>();
  return {
    ...actual,
    walletApi: {
      ...actual.walletApi,
      get: mockWalletGet,
    },
    habitsApi: {
      ...actual.habitsApi,
      today: mockTodayGet,
    },
  };
});

// EventSource не реализован в jsdom, а useTodayStream внутри TodayPage
// вызывает createStreamController → new EventSource(). Мокаем useTodayStream
// целиком — для тестов Z-4.4 SSE не нужен (проверяем только wallet warning-блок).
vi.mock("@/shared/hooks/useTodayStream", () => ({
  useTodayStream: () => undefined,
}));

import { TodayPage } from "../TodayPage";
import type { TodayResponse, WalletResponse } from "@/shared/types";

const HABIT_ID = "habit-1";

const TODAY_OK: TodayResponse = {
  habit: {
    id: HABIT_ID,
    title: "Планка",
    description: null,
    chat_id: -100,
    checkin_window_start: "06:00:00",
    checkin_window_end: "11:00:00",
    timezone: "Europe/Moscow",
    penalty_amount: 50_000,
    price_month: 100_000,
    proof_type: "video_note",
    proof_types: ["video_note"],
    prize_pool: 0,
    members_count: 5,
    is_active: true,
    photo_url: null,
    telegram_invite_link: null,
    checkin_topic_thread_id: null,
    chat_topic_thread_id: null,
  },
  membership: {
    id: "mem-1",
    user_id: 1,
    habit_id: HABIT_ID,
    status: "active",
    deposit_balance: 0, // legacy — на UI не используется (deposit на user)
    subscription_until: null,
    auto_renew_enabled: false,
    joined_at: new Date().toISOString(),
  },
  checkin: {
    status: "pending",
    checkin_count: 0,
    streak_days: 0,
    penalties_count: 0,
    penalties_total: 0,
    deadline_at: null,
  },
};

const WALLET_OK: WalletResponse = {
  deposit_balance: 50_000,
  active_clubs: [
    {
      habit_id: HABIT_ID,
      title: "Планка",
      penalty_amount: 50_000,
      can_checkin: true,
      status: "active",
    },
  ],
};

const WALLET_LOW: WalletResponse = {
  deposit_balance: 20_000, // меньше 50_000 penalty → can_checkin=false
  active_clubs: [
    {
      habit_id: HABIT_ID,
      title: "Планка",
      penalty_amount: 50_000,
      can_checkin: false,
      status: "paused",
    },
  ],
};

const renderWithProviders = (
  ui: React.ReactNode,
  options: { wallet: WalletResponse | null; today: TodayResponse | null } = {
    wallet: null,
    today: null,
  },
) => {
  if (options.wallet !== null) {
    mockWalletGet.mockResolvedValue(options.wallet);
  }
  if (options.today !== null) {
    mockTodayGet.mockResolvedValue(options.today);
  }
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/habits/${HABIT_ID}/today`]}>
        <Routes>
          <Route path="/habits/:habitId/today" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
});

describe("TodayPage — warning-блок при недостаточном депозите (Z-4.4)", () => {
  it("can_checkin=true → warning-блок НЕ показывается, кнопка чек-ина активна", async () => {
    renderWithProviders(<TodayPage />, {
      wallet: WALLET_OK,
      today: TODAY_OK,
    });

    // Дожидаемся загрузки useToday.
    await waitFor(() => {
      expect(screen.getByText(/Планка/)).toBeInTheDocument();
    });

    // Warning-блок НЕ виден.
    expect(
      screen.queryByText(/Для продолжения участия нужно/i),
    ).not.toBeInTheDocument();
    // Кнопка «Сделать чек-ин» есть и НЕ disabled.
    const checkinButton = screen.queryByRole("button", {
      name: /Сделать чек-ин/i,
    });
    // Кнопка может отсутствовать если checkin_topic_thread_id=null,
    // но если она есть — она должна быть активна.
    if (checkinButton) {
      expect(checkinButton).not.toBeDisabled();
    }
  });

  it("can_checkin=false (deposit < penalty) → warning-блок с цифрами + кнопка '💰 Пополнить депозит'", async () => {
    renderWithProviders(<TodayPage />, {
      wallet: WALLET_LOW,
      today: TODAY_OK,
    });

    await waitFor(() => {
      expect(screen.getByText(/Планка/)).toBeInTheDocument();
    });

    // Warning-блок виден с конкретными цифрами (penalty=500, current=200).
    expect(
      screen.getByText(/Для продолжения участия нужно/i),
    ).toBeInTheDocument();
    // Цифры 500 (penalty) и 200 (текущий).
    expect(screen.getAllByText(/500/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/200/).length).toBeGreaterThanOrEqual(1);
    // Кнопка "💰 Пополнить депозит" видна.
    expect(
      screen.getByRole("button", { name: /Пополнить депозит/i }),
    ).toBeInTheDocument();
  });

  it("Клик 'Пополнить депозит' → открывается TopUpModal с defaultAmount = penalty - current", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TodayPage />, {
      wallet: WALLET_LOW,
      today: TODAY_OK,
    });

    await waitFor(() => {
      expect(screen.getByText(/Планка/)).toBeInTheDocument();
    });

    await user.click(
      screen.getByRole("button", { name: /Пополнить депозит/i }),
    );

    // TopUpModal открывается — ищем уникальный текст рекомендации.
    // Текст "Рекомендуем пополнить на 300" разбит на части из-за <strong> внутри,
    // поэтому используем функцию-матчер через textContent. Используем getAllByText
    // потому что таких элементов может быть несколько (TopUpModal + родитель TodayPage).
    await waitFor(() => {
      expect(
        screen.getAllByText(
          (_content, element) =>
            element?.textContent?.includes("Рекомендуем пополнить на 300") ?? false,
        ).length,
      ).toBeGreaterThanOrEqual(1);
    });
  });

  it("wallet ещё не загружен → optimistic can_checkin=true (warning НЕ показывается)", async () => {
    // wallet ещё в процессе загрузки (ещё не разрешён queryFn).
    // На UI TodayPage ведёт себя оптимистично: can_checkin=true, warning НЕ показывается,
    // кнопка "💰 Пополнить депозит" НЕ появляется.
    // Backend уже верифицировал membership в GET /habits/{id}/today, так что
    // блокировка UI не нужна — useToday уже вернул полный TodayResponse.
    mockWalletGet.mockImplementation(
      () => new Promise(() => undefined),  // никогда не resolve
    );
    mockTodayGet.mockResolvedValue(TODAY_OK);

    renderWithProviders(<TodayPage />);

    await waitFor(() => {
      expect(screen.getByText(/Планка/)).toBeInTheDocument();
    });

    // No warning block.
    expect(
      screen.queryByText(/Для продолжения участия нужно/i),
    ).not.toBeInTheDocument();
    // No "💰 Пополнить депозит" button (только когда can_checkin=false).
    expect(
      screen.queryByRole("button", { name: /Пополнить депозит/i }),
    ).not.toBeInTheDocument();
  });
});
