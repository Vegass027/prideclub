// @vitest-environment jsdom
// Pravki-paused-frontend-2026-08-14: тест UX-фильтра «paused НЕ в violators».
//
// Минимальные кейсы для проверки контракта:
// (а) membership_status='paused' + can_catch=true → НЕ в «Можно поймать».
// (б) membership_status='active' + can_catch=true → в «Можно поймать».
// (в) membership_status='left' + can_catch=true → НЕ в «Можно поймать»
//     (защита от дрейфа: left тоже не должен быть кандидатом).
//
// Backend race-condition защита — на стороне server (MembershipNotActiveError
// + re-check после user-lock). Frontend-фильтр — это UX, не security.
// Если backend пропустит некорректный catch — фронт покажет toast
// «Членство не активно», а не бессмысленную синюю кнопку «Поймать».
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// vi.mock фабрика хойстится в начало файла — все переменные должны
// объявляться через vi.hoisted() чтобы попасть в фабрику мока.
const { mockMembersList } = vi.hoisted(() => ({
  mockMembersList: vi.fn(),
}));

vi.mock("@/shared/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/api")>();
  return {
    ...actual,
    membersApi: {
      ...actual.membersApi,
      list: mockMembersList,
    },
  };
});

// EventSource не реализован в jsdom — мокаем useHabitSse целиком.
// Pravki-paused-frontend-2026-08-14: MembersPage теперь тоже использует
// useHabitSse для real-time invalidate списка на catch event. Тесты тут
// проверяют только UI-логику violators-фильтра; SSE invalidate
// покрывается интеграционными тестами и проверкой в Pravki-deposit-sse.
vi.mock("@/shared/hooks/useHabitSse", () => ({
  useHabitSse: () => undefined,
}));

import { MembersPage } from "../MembersPage";
import type { MemberRow } from "@/shared/types";

const HABIT_ID = "habit-1";

// Хелпер для построения фикстуры — все поля обязательны (TS strict).
const makeRow = (overrides: Partial<MemberRow>): MemberRow => ({
  membership_id: overrides.membership_id ?? "mem-default",
  user_id: overrides.user_id ?? 0,
  first_name: overrides.first_name ?? "User",
  username: overrides.username ?? null,
  status: overrides.status ?? "missed",
  checkin_count: overrides.checkin_count ?? 0,
  can_catch: overrides.can_catch ?? true,
  membership_status: overrides.membership_status ?? "active",
  photo_url: overrides.photo_url ?? null,
});

const renderWithProviders = (habitId: string) => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/habits/${habitId}/members`]}>
        <Routes>
          <Route path="/habits/:habitId/members" element={<MembersPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

// MembersPage использует hapticImpact/hapticNotify из telegram/tma —
// в jsdom нет window.Telegram, функции no-op через try/catch внутри.
// window.alert тоже мокается, чтобы случайные catch-error'ы не падали.

beforeEach(() => {
  vi.spyOn(window, "alert").mockImplementation(() => undefined);
  mockMembersList.mockReset();
});

describe("MembersPage — UX-фильтр 'paused не в violators' (2026-08-14)", () => {
  it("(а) paused + can_catch=true → НЕ в «Можно поймать», виден в общем списке", async () => {
    mockMembersList.mockResolvedValue({
      items: [
        makeRow({
          membership_id: "mem-paused-1",
          user_id: 100,
          first_name: "Аня",
          status: "missed",
          can_catch: true,
          membership_status: "paused",
        }),
      ],
    });

    renderWithProviders(HABIT_ID);

    // Ждём загрузки — сначала появляется «Все участники (1)» с именем Ани.
    await waitFor(() => {
      expect(screen.getByText("Аня")).toBeInTheDocument();
    });

    // Заголовок секции «Можно поймать» НЕ должен рендериться
    // (violators.length > 0 → false → <section> не возвращается).
    expect(
      screen.queryByText(/Можно поймать/i),
    ).not.toBeInTheDocument();

    // Кнопка «Поймать» НЕ должна существовать — именно ради этого
    // сделан фильтр (UX, не security): paused-юзер скрыт из списка ловли.
    expect(
      screen.queryByRole("button", { name: /^Поймать/i }),
    ).not.toBeInTheDocument();

    // Аня остаётся видимой в общем списке участников клуба
    // (не путать с «Можно поймать»). Ищем в <section>, где
    // header «Все участники (N)».
    const othersSectionHeading = screen.getByText(/Все участники/i);
    expect(othersSectionHeading).toBeInTheDocument();
    // sanity-check: «Все участники (1)»
    expect(othersSectionHeading.textContent).toMatch(/\(1\)/);
  });

  it("(б) active + can_catch=true → попадает в «Можно поймать» с кнопкой", async () => {
    mockMembersList.mockResolvedValue({
      items: [
        makeRow({
          membership_id: "mem-active-1",
          user_id: 200,
          first_name: "Борис",
          status: "missed",
          can_catch: true,
          membership_status: "active",
        }),
      ],
    });

    renderWithProviders(HABIT_ID);

    // Дожидаемся появления имени Бориса — загрузка завершена.
    await waitFor(() => {
      expect(screen.getByText("Борис")).toBeInTheDocument();
    });

    // Заголовок «Можно поймать» с числом 1 (одна цель).
    const violatorsHeading = screen.getByText(/Можно поймать/i);
    expect(violatorsHeading).toBeInTheDocument();
    expect(violatorsHeading.textContent).toMatch(/\(1\)/);

    // Кнопка «Поймать Борис» (по aria-label) видна и активна.
    const catchButton = screen.getByRole("button", {
      name: /^Поймать Борис$/i,
    });
    expect(catchButton).toBeInTheDocument();
    expect(catchButton).not.toBeDisabled();

    // «Все участники» показывает empty-state — Борис попал в violators
    // и НЕ в others (others = items.filter(!violators.includes(m))).
    // Empty-state copy: «Тут пока никого кроме тебя».
    expect(
      screen.getByText(/Тут пока никого кроме тебя/i),
    ).toBeInTheDocument();
  });

  it("(в) left + can_catch=true → НЕ в «Можно поймать» (защита от дрейфа)", async () => {
    // can_catch=true — это ВОЗМОЖНО если backend не учёл LEFT статус в can_catch
    // (left обычно нет в /members, но контракт фильтра должен быть defensive).
    mockMembersList.mockResolvedValue({
      items: [
        makeRow({
          membership_id: "mem-left-1",
          user_id: 300,
          first_name: "Вика",
          status: "missed",
          can_catch: true,
          membership_status: "left",
        }),
      ],
    });

    renderWithProviders(HABIT_ID);

    await waitFor(() => {
      expect(screen.getByText("Вика")).toBeInTheDocument();
    });

    // LEFT не в violators — даже если backend рассчитал can_catch=true,
    // фронт всё равно уберёт кнопку.
    expect(
      screen.queryByText(/Можно поймать/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Поймать/i }),
    ).not.toBeInTheDocument();

    // Вика остаётся видимой в общем списке — но через фильтр
    // violators=[] others содержит Вику.
    expect(screen.getByText(/Все участники/i)).toBeInTheDocument();
  });

  it("(г) mixed-список: 1 active + 1 paused + 1 active с other.status → active+missed в violators, paused вне", async () => {
    // Реалистичный сценарий: трое участников, разные статусы.
    //   - Аня: paused, missed, can_catch=true → должна быть скрыта из violators.
    //   - Борис: active, missed, can_catch=true → в violators.
    //   - Вика: active, done (уже отметился), can_catch=false → вне violators.
    mockMembersList.mockResolvedValue({
      items: [
        makeRow({
          membership_id: "mem-paused-1",
          user_id: 100,
          first_name: "Аня",
          status: "missed",
          can_catch: true,
          membership_status: "paused",
        }),
        makeRow({
          membership_id: "mem-active-missed",
          user_id: 200,
          first_name: "Борис",
          status: "missed",
          can_catch: true,
          membership_status: "active",
        }),
        makeRow({
          membership_id: "mem-active-done",
          user_id: 300,
          first_name: "Вика",
          status: "done",
          can_catch: false,
          membership_status: "active",
        }),
      ],
    });

    renderWithProviders(HABIT_ID);

    await waitFor(() => {
      // Должны быть видны все три имени в DOM (others содержит всех).
      expect(screen.getByText("Аня")).toBeInTheDocument();
      expect(screen.getByText("Борис")).toBeInTheDocument();
      expect(screen.getByText("Вика")).toBeInTheDocument();
    });

    // «Можно поймать (1)» — только Борис.
    const violatorsHeading = screen.getByText(/Можно поймать/i);
    expect(violatorsHeading.textContent).toMatch(/\(1\)/);

    // Проверяем, что в violators-секции только Борис, не Аня.
    const violatorsSection = violatorsHeading.parentElement as HTMLElement;
    expect(within(violatorsSection).getByText("Борис")).toBeInTheDocument();
    expect(within(violatorsSection).queryByText("Аня")).not.toBeInTheDocument();

    // Только одна кнопка «Поймать» — для Бориса. Имя юзера передаётся
    // через aria-label (accessible name), не через textContent (кнопка
    // показывает статичный текст «Поймать»). Поэтому используем
    // role+name matcher.
    const catchButtons = screen.getAllByRole("button", {
      name: /^Поймать Борис$/i,
    });
    expect(catchButtons).toHaveLength(1);
  });
});
