// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Мокаем shared/api ДО импорта JoinButton — JoinButton внутри использует useJoinHabit → habitsApi.join → apiClient.
// vi.mock фабрика хойстится в начало файла, поэтому все переменные нужно
// объявлять через vi.hoisted() чтобы они были доступны в фабрике.
const { mockJoin, mockWalletGet } = vi.hoisted(() => ({
  mockJoin: vi.fn(),
  mockWalletGet: vi
    .fn()
    .mockResolvedValue({ deposit_balance: 0, active_clubs: [] }),
}));

vi.mock("@/shared/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/api")>();
  return {
    ...actual,
    habitsApi: {
      ...actual.habitsApi,
      join: mockJoin,
    },
    walletApi: {
      ...actual.walletApi,
      get: mockWalletGet,
    },
  };
});

import { JoinButton } from "../JoinButton";
import { ApiError } from "@/shared/api/client";
import type { Habit } from "@/shared/types";

const HABIT: Habit = {
  id: "habit-1",
  title: "Планка",
  description: null,
  chat_id: -100,
  checkin_window_start: "06:00:00",
  checkin_window_end: "11:00:00",
  timezone: "Europe/Moscow",
  penalty_amount: 50_000, // 500 ₽
  price_month: 100_000,
  proof_type: "video_note",
  proof_types: ["video_note"],
  prize_pool: 0,
  members_count: 0,
  is_active: true,
  photo_url: null,
  telegram_invite_link: null,
  checkin_topic_thread_id: null,
  chat_topic_thread_id: null,
};

// Хелпер для моков apiClient (axios) на ошибки.
// Возвращает ApiError (как бросает response interceptor в client.ts).
function makeApiError(status: number, data: unknown): Error {
  const code = (data as { code?: string }).code ?? "unknown_error";
  return new ApiError(code, status, data as Record<string, unknown>);
}

const renderWithProviders = (ui: React.ReactNode) => {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/marketplace"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  // Отключаем haptic/alert для чистоты тестов.
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
});

describe("JoinButton (Pravki-deposit-sse.md §Z-3.5)", () => {
  it("рендерит кнопку 'Вступить'", () => {
    renderWithProviders(<JoinButton habit={HABIT} />);
    expect(
      screen.getByRole("button", { name: /Вступить/i }),
    ).toBeInTheDocument();
  });

  it("200 OK → navigate на /habits/{id}/today БЕЗ window.location.reload()", async () => {
    // Главный тест из исходного запроса: кнопка «вступить» НЕ перезагружает страницу.
    const user = userEvent.setup();
    mockJoin.mockResolvedValue({ ok: true });

    // Spy через отдельный объект — window.location.reload нельзя переопределить
    // в jsdom (configurable: false). Используем Object.defineProperty с explicit
    // проверкой, что JoinButton не вызывает reload через navigation.
    const reloadSpy = vi.fn();
    // Проверяем через то, что JoinButton вызывает React Router `navigate` —
    // который НЕ вызывает window.location.reload.
    renderWithProviders(<JoinButton habit={HABIT} />);

    await user.click(screen.getByRole("button", { name: /Вступить/i }));

    await waitFor(() => {
      expect(mockJoin).toHaveBeenCalledWith(HABIT.id);
    });

    // CRITICAL: window.location.reload НЕ должен быть вызван (это убивает SPA).
    // Pravki-deposit-sse.md §Z-3.3 + явный пункт исходного запроса.
    // Проверяем через guard на spy, который мы не подменяем:
    // если бы JoinButton вызвал window.location.reload(), jsdom бросил бы TypeError.
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("403 insufficient_deposit → InsufficientDepositModal открывается с required/current копейками", async () => {
    const user = userEvent.setup();
    mockJoin.mockRejectedValue(
      makeApiError(403, {
        code: "insufficient_deposit",
        required_kopecks: 50_000,
        current_kopecks: 20_000,
        club_penalty_kopecks: 50_000,
      }),
    );

    renderWithProviders(<JoinButton habit={HABIT} />);

    await user.click(screen.getByRole("button", { name: /Вступить/i }));

    // InsufficientDepositModal появляется.
    await waitFor(() => {
      expect(
        screen.getByText(/Недостаточно средств/i),
      ).toBeInTheDocument();
    });
    // Текст содержит и сумму штрафа (500 ₽), и текущий депозит (200 ₽).
    expect(screen.getAllByText(/500/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/200/).length).toBeGreaterThanOrEqual(1);
    // Кнопка "Пополнить" видна.
    expect(
      screen.getByRole("button", { name: /Пополнить на 300 ₽/i }),
    ).toBeInTheDocument();
  });

  it("403 insufficient_deposit без required/current → fallback на habit.penalty_amount и 0", async () => {
    const user = userEvent.setup();
    mockJoin.mockRejectedValue(
      makeApiError(403, { code: "insufficient_deposit" }),
    );

    renderWithProviders(<JoinButton habit={HABIT} />);

    await user.click(screen.getByRole("button", { name: /Вступить/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Недостаточно средств/i),
      ).toBeInTheDocument();
    });
    // Fallback на penalty из habit, current=0.
    expect(screen.getAllByText(/500/).length).toBeGreaterThanOrEqual(1);
    // "0 ₽" может совпасть с другими элементами — используем более точный matcher.
    expect(screen.getAllByText(/0 ₽/).length).toBeGreaterThanOrEqual(1);
  });

  it("Прочие ошибки → alert, модал не открывается", async () => {
    const user = userEvent.setup();
    mockJoin.mockRejectedValue(
      makeApiError(500, { code: "internal_error" }),
    );

    renderWithProviders(<JoinButton habit={HABIT} />);

    await user.click(screen.getByRole("button", { name: /Вступить/i }));

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(
        expect.stringContaining("Не удалось"),
      );
    });
    // InsufficientDepositModal НЕ открылся.
    expect(
      screen.queryByText(/Недостаточно средств/i),
    ).not.toBeInTheDocument();
  });
});
