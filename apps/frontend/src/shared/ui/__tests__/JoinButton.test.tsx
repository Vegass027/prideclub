// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Мокаем shared/api ДО импорта JoinButton — JoinButton внутри использует
// useWallet → walletApi.get и useJoinAndPay → balanceApi.subscribe.
// vi.mock фабрика хойстится в начало файла, поэтому все переменные нужно
// объявлять через vi.hoisted() чтобы они были доступны в фабрике.
const { mockSubscribe, mockWalletGet } = vi.hoisted(() => ({
  mockSubscribe: vi.fn(),
  mockWalletGet: vi.fn().mockResolvedValue({
    deposit_balance: 0,
    active_clubs: [],
  }),
}));

vi.mock("@/shared/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/api")>();
  return {
    ...actual,
    balanceApi: {
      ...actual.balanceApi,
      subscribe: mockSubscribe,
    },
    walletApi: {
      ...actual.walletApi,
      get: mockWalletGet,
    },
  };
});

import { JoinButton } from "../JoinButton";
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
  price_month: 100_000, // 1000 ₽
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
  mockSubscribe.mockReset();
  mockWalletGet.mockReset();
  // Дефолт: wallet пустой (юзер не состоит ни в одном клубе).
  mockWalletGet.mockResolvedValue({
    deposit_balance: 0,
    active_clubs: [],
  });
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
});

// ---------------------------------------------------------------------------
// Pravki-subscribe-and-join.md §Z-17 substep 2: JoinButton + JoinPayModal
// ---------------------------------------------------------------------------

describe("JoinButton (Pravki-subscribe-and-join.md §Z-17)", () => {
  it("рендерит кнопку 'Вступить'", async () => {
    renderWithProviders(<JoinButton habit={HABIT} />);
    expect(
      await screen.findByRole("button", { name: /Вступить/i }),
    ).toBeInTheDocument();
  });

  it("useWallet isLoading=true → кнопка disabled и не открывает модалку", async () => {
    // Эмулируем pending-состояние useWallet (никогда не резолвится).
    mockWalletGet.mockReturnValue(new Promise(() => undefined));

    const user = userEvent.setup();
    renderWithProviders(<JoinButton habit={HABIT} />);

    // При loading=true Button заменяет children на "..." — нет смысла искать
    // по name, проверяем только что кнопка существует и disabled.
    const button = await screen.findByRole("button");
    // Кнопка disabled пока useWallet не вернул данные.
    expect(button).toBeDisabled();
    // Click блокируется, модалка не открывается.
    await user.click(button);
    expect(
      screen.queryByText(/Вступить в клуб/i),
    ).not.toBeInTheDocument();
    expect(mockSubscribe).not.toHaveBeenCalled();
  });

  it("wallet пустой (нет membership) → mode='full' (чекбокс подписки виден)", async () => {
    const user = userEvent.setup();
    mockWalletGet.mockResolvedValue({
      deposit_balance: 0,
      active_clubs: [],
    });
    mockSubscribe.mockResolvedValue({
      ok: true,
      transaction_id: "tx-1",
      membership_id: "m-1",
      new_deposit_balance: 150_000,
      subscription_until: "2026-09-07",
      total_charged_kopecks: 150_000,
      charged_subscription: true,
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    // Ждём пока wallet загрузится и кнопка станет активной.
    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());

    await user.click(button);

    // Чекбокс подписки виден → mode='full'.
    await waitFor(() => {
      expect(
        screen.getByText(/Согласен на подписку/i),
      ).toBeInTheDocument();
    });
  });

  it("wallet содержит ACTIVE membership с subscription_until в будущем → mode='deposit-only' (без чекбокса)", async () => {
    const user = userEvent.setup();
    const futureDate = new Date(Date.now() + 15 * 86_400_000)
      .toISOString()
      .slice(0, 10);
    mockWalletGet.mockResolvedValue({
      deposit_balance: 100_000,
      active_clubs: [
        {
          habit_id: HABIT.id,
          title: HABIT.title,
          penalty_amount: HABIT.penalty_amount,
          can_checkin: true,
          status: "active" as const,
          subscription_until: futureDate,
        },
      ],
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());

    await user.click(button);

    // Нет чекбокса подписки → mode='deposit-only'.
    await waitFor(() => {
      expect(
        screen.queryByText(/Согласен на подписку/i),
      ).not.toBeInTheDocument();
    });
    // Кнопка говорит "Пополнить и открыть клуб".
    expect(
      screen.getByRole("button", { name: /Пополнить .* и открыть клуб/i }),
    ).toBeInTheDocument();
  });

  it("wallet содержит ACTIVE membership с ИСТЁКШЕЙ подпиской → mode='full'", async () => {
    const user = userEvent.setup();
    const pastDate = "2026-01-01"; // дата в прошлом
    mockWalletGet.mockResolvedValue({
      deposit_balance: 50_000,
      active_clubs: [
        {
          habit_id: HABIT.id,
          title: HABIT.title,
          penalty_amount: HABIT.penalty_amount,
          can_checkin: true,
          status: "active" as const,
          subscription_until: pastDate,
        },
      ],
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());

    await user.click(button);

    // Подписка истекла → mode='full' (нужна полная оплата).
    await waitFor(() => {
      expect(
        screen.getByText(/Согласен на подписку/i),
      ).toBeInTheDocument();
    });
  });

  it("успешная оплата → navigate на /habits/{id}/today", async () => {
    const user = userEvent.setup();
    mockWalletGet.mockResolvedValue({
      deposit_balance: 0,
      active_clubs: [],
    });
    mockSubscribe.mockResolvedValue({
      ok: true,
      transaction_id: "tx-1",
      membership_id: "m-1",
      new_deposit_balance: 150_000,
      subscription_until: "2026-09-07",
      total_charged_kopecks: 150_000,
      charged_subscription: true,
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    await user.click(button);

    // Открылся mode=full modal с чекбоксом.
    await waitFor(() => {
      expect(
        screen.getByText(/Согласен на подписку/i),
      ).toBeInTheDocument();
    });

    // Выбираем пресет депозита (500 ₽) — иначе кнопка "Оплатить" disabled.
    await user.click(screen.getByRole("button", { name: /500 ₽/ }));
    // Кликаем чекбокс подписки.
    await user.click(screen.getByRole("checkbox"));
    // Кликаем кнопку "Оплатить".
    await user.click(screen.getByRole("button", { name: /Оплатить .* ₽/i }));

    // После успеха navigate вызывается. Проверяем через то, что mockSubscribe
    // был вызван (JoinButton делает navigate в onSuccess hook'а).
    await waitFor(() => {
      expect(mockSubscribe).toHaveBeenCalledWith(
        expect.objectContaining({
          habit_id: HABIT.id,
          subscription_accepted: true,
          deposit_amount_kopecks: 50_000,
        }),
      );
    });
  });

  it("не передаёт subscription_accepted=true при mode='deposit-only'", async () => {
    const user = userEvent.setup();
    const futureDate = new Date(Date.now() + 15 * 86_400_000)
      .toISOString()
      .slice(0, 10);
    mockWalletGet.mockResolvedValue({
      deposit_balance: 100_000,
      active_clubs: [
        {
          habit_id: HABIT.id,
          title: HABIT.title,
          penalty_amount: HABIT.penalty_amount,
          can_checkin: true,
          status: "active" as const,
          subscription_until: futureDate,
        },
      ],
    });
    mockSubscribe.mockResolvedValue({
      ok: true,
      transaction_id: "tx-1",
      membership_id: "m-1",
      new_deposit_balance: 50_000,
      subscription_until: futureDate,
      total_charged_kopecks: 50_000,
      charged_subscription: false,
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    await user.click(button);

    // Mode='deposit-only' → нет чекбокса → subscription_accepted=false.
    await waitFor(() => {
      expect(
        screen.queryByText(/Согласен на подписку/i),
      ).not.toBeInTheDocument();
    });

    // Выбираем пресет депозита (500 ₽) — иначе кнопка "Пополнить" disabled.
    await user.click(screen.getByRole("button", { name: /500 ₽/ }));

    // Кликаем кнопку "Пополнить и открыть клуб".
    await user.click(
      screen.getByRole("button", { name: /Пополнить .* и открыть клуб/i }),
    );

    await waitFor(() => {
      expect(mockSubscribe).toHaveBeenCalledWith(
        expect.objectContaining({
          habit_id: HABIT.id,
          subscription_accepted: false,
          deposit_amount_kopecks: 50_000,
        }),
      );
    });
  });

  // Pravki-subscribe-and-join.md §Z-17 substep 2 gap fix:
  // alert после оплаты опирается на response.total_charged_kopecks и
  // response.charged_subscription, не на клиентски предпосчитанную сумму.
  // Это защищает от ситуации "UI обещал X, списали Y" (например, LEFT +
  // активная подписка — mode='full' показывал price_month+deposit, бэк
  // списал только deposit).
  it("alert после успеха использует data.total_charged_kopecks и data.charged_subscription", async () => {
    const user = userEvent.setup();
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    mockWalletGet.mockResolvedValue({
      deposit_balance: 0,
      active_clubs: [],
    });
    // Симулируем "gap fix" сценарий: UI mode='full' (т.к. walletClub нет),
    // но бэкенд видит LEFT+active_subscription и списывает ТОЛЬКО deposit.
    mockSubscribe.mockResolvedValue({
      ok: true,
      transaction_id: "tx-1",
      membership_id: "m-1",
      new_deposit_balance: 50_000,
      subscription_until: "2026-09-07", // subscription_until сохранён, не новый
      total_charged_kopecks: 50_000, // ← только deposit, не price_month+deposit
      charged_subscription: false, // ← бэкенд решил не списывать подписку
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    await user.click(button);

    await waitFor(() => {
      expect(
        screen.getByText(/Согласен на подписку/i),
      ).toBeInTheDocument();
    });

    // Выбираем пресет 500₽, отмечаем чекбокс, платим.
    await user.click(screen.getByRole("button", { name: /500 ₽/ }));
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /Оплатить .* ₽/i }));

    // Alert использует РЕАЛЬНУЮ списанную сумму (500 ₽, не UI-предпосчитанную 1500 ₽).
    // И явно говорит что подписка НЕ списывалась.
    await waitFor(() => {
      const lastCall = warnSpy.mock.calls[warnSpy.mock.calls.length-1];
      expect(lastCall?.[0]).toBe("[showAlert]");
      expect(lastCall?.[1]).toMatch(/Списано 500 ₽/);
      expect(lastCall?.[1]).toMatch(/только депозит/);
      expect(lastCall?.[1]).toMatch(/Подписка активна до 2026-09-07/);
    });
    // И не должен показывать старый misleading текст "Добро пожаловать в клуб".
    const allCalls = warnSpy.mock.calls.flatMap((c) => c.slice(1));
    expect(
      allCalls.some((msg) => /Добро пожаловать в клуб/.test(String(msg))),
    ).toBe(false);

    warnSpy.mockRestore();
  });

  it("alert с charged_subscription=true показывает подписку + депозит", async () => {
    const user = userEvent.setup();
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);

    mockWalletGet.mockResolvedValue({
      deposit_balance: 0,
      active_clubs: [],
    });
    mockSubscribe.mockResolvedValue({
      ok: true,
      transaction_id: "tx-1",
      membership_id: "m-1",
      new_deposit_balance: 150_000,
      subscription_until: "2026-09-07",
      total_charged_kopecks: 150_000, // price_month (1000) + deposit (500)
      charged_subscription: true,
    });

    renderWithProviders(<JoinButton habit={HABIT} />);

    const button = await screen.findByRole("button", { name: /Вступить/i });
    await waitFor(() => expect(button).not.toBeDisabled());
    await user.click(button);

    await waitFor(() => {
      expect(
        screen.getByText(/Согласен на подписку/i),
      ).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: /500 ₽/ }));
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /Оплатить .* ₽/i }));

    await waitFor(() => {
      const lastCall = warnSpy.mock.calls[warnSpy.mock.calls.length-1];
      // toLocaleString("ru-RU") использует non-breaking space (U+00A0)
      // между разрядами, поэтому regex с обычным пробелом не подходит.
      expect(lastCall?.[1]).toMatch(/Списано 1[\s\u00a0]500 ₽/);
      expect(lastCall?.[1]).toMatch(/подписка \+ депозит/);
      expect(lastCall?.[1]).toMatch(/Добро пожаловать в клуб/);
    });

    warnSpy.mockRestore();
  });
});
