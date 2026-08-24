// @vitest-environment jsdom
// Pravki-catcher-deposit (Phase 1 Task 1.6, 2026-08-21): vitest-тест для
// HabitEditForm — проверка что новое поле catcher_amount_kopecks_rub
// корректно отображается при загрузке (kopToRubStr) и сохраняется
// в payload update (rubToKopecks, int копейки).
//
// Паттерн скопирован с MembersPage.test.tsx (jsdom + vi.hoisted + vi.mock).
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// vi.mock фабрика хойстится в начало файла.
const { mockUpdate, mockGet } = vi.hoisted(() => ({
  mockUpdate: vi.fn(),
  mockGet: vi.fn(),
}));

vi.mock("@/admin/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/admin/api")>();
  return {
    ...actual,
    adminHabitsApi: {
      ...actual.adminHabitsApi,
      update: mockUpdate,
      get: mockGet,
    },
  };
});

// useNavigate мокаем чтобы не падать на отсутствующем роуте после submit.
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

import { HabitEditForm } from "../HabitEditForm";
import type { AdminHabit } from "@/admin/api";

// Хелпер для построения фикстуры — все поля обязательны (TS strict).
function makeHabit(overrides: Partial<AdminHabit> = {}): AdminHabit {
  return {
    id: "habit-1",
    title: "Клуб «Тест»",
    description: "Test desc",
    chat_id: 100,
    checkin_window_start: "09:00:00",
    checkin_window_end: "21:00:00",
    timezone: "Europe/Moscow",
    penalty_amount: 30000, // 300₽
    catcher_amount_kopecks: 0, // default для новых клубов
    price_month: 1000_00, // 1000₽
    proof_type: "video_note",
    proof_types: ["video_note"],
    prize_pool: 0,
    is_active: true,
    photo_url: null,
    telegram_invite_link: null,
    stat_name: "Дисциплина",
    stat_icon: "🔥",
    stat_gain_per_checkin: 2,
    stat_loss_per_miss: 1,
    member_limit: null,
    curator_id: null,
    checkin_topic_thread_id: 1,
    notifications_topic_thread_id: 2,
    chat_topic_thread_id: null,
    checkin_topic_link: null,
    notifications_topic_link: null,
    chat_topic_link: null,
    archived_at: null,
    created_at: "2026-08-21T10:00:00Z",
    active_members_count: 0,
    ...overrides,
  };
}

function renderForm(habit: AdminHabit | null) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/habits/habit-1/edit"]}>
        <Routes>
          <Route
            path="/habits/:id/edit"
            element={
              <HabitEditForm habit={habit} loading={false} error={null} />
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockUpdate.mockReset();
  mockUpdate.mockResolvedValue(undefined);
});

describe("HabitEditForm — catcher_amount_rub (Pravki-catcher-deposit Task 1.6)", () => {
  it("показывает '0' для свежего клуба с catcher_amount_kopecks=0", async () => {
    const habit = makeHabit({ catcher_amount_kopecks: 0 });
    renderForm(habit);

    // kopToRubStr(0) → "0"
    const input = await screen.findByDisplayValue("0");
    expect(input).toBeInTheDocument();
  });

  it("показывает '100' для клуба с catcher_amount_kopecks=10000 (100₽)", async () => {
    const habit = makeHabit({ catcher_amount_kopecks: 10000 });
    renderForm(habit);

    // kopToRubStr(10000) → "100"
    const input = await screen.findByDisplayValue("100");
    expect(input).toBeInTheDocument();
  });

  it("показывает '75.5' для клуба с catcher_amount_kopecks=7550 (75.5₽)", async () => {
    const habit = makeHabit({ catcher_amount_kopecks: 7550 });
    renderForm(habit);

    // kopToRubStr(7550) → "75.5"
    const input = await screen.findByDisplayValue("75.5");
    expect(input).toBeInTheDocument();
  });

  it(
    "submit отправляет catcher_amount_kopecks в копейках (100₽ → 10000)",
    async () => {
      const habit = makeHabit({ catcher_amount_kopecks: 0 });
      renderForm(habit);

      // Находим поле по label (jsdom-dom)
      const input = await screen.findByDisplayValue("0");
      // Меняем на 100₽
      fireEvent.change(input, { target: { value: "100" } });

      // Submit form
      const form = input.closest("form");
      expect(form).not.toBeNull();
      fireEvent.submit(form!);

      await waitFor(() => {
        expect(mockUpdate).toHaveBeenCalledTimes(1);
      });
      const payload = mockUpdate.mock.calls[0][1];
      expect(payload.catcher_amount_kopecks).toBe(10000); // 100₽ = 10000 коп
      expect(payload.penalty_amount).toBe(30000); // sanity check
    },
  );

  it(
    "submit с default 0 (клуб без явной настройки) → catcher_amount_kopecks=0 в payload",
    async () => {
      const habit = makeHabit({ catcher_amount_kopecks: 0 });
      renderForm(habit);

      const input = await screen.findByDisplayValue("0");
      const form = input.closest("form");
      expect(form).not.toBeNull();
      fireEvent.submit(form!);

      await waitFor(() => {
        expect(mockUpdate).toHaveBeenCalledTimes(1);
      });
      const payload = mockUpdate.mock.calls[0][1];
      expect(payload.catcher_amount_kopecks).toBe(0);
    },
  );

  it(
    "submit с дробной суммой (75.5₽) → catcher_amount_kopecks=7550 в payload",
    async () => {
      const habit = makeHabit({ catcher_amount_kopecks: 0 });
      renderForm(habit);

      const input = await screen.findByDisplayValue("0");
      fireEvent.change(input, { target: { value: "75.5" } });
      const form = input.closest("form");
      expect(form).not.toBeNull();
      fireEvent.submit(form!);

      await waitFor(() => {
        expect(mockUpdate).toHaveBeenCalledTimes(1);
      });
      const payload = mockUpdate.mock.calls[0][1];
      expect(payload.catcher_amount_kopecks).toBe(7550); // 75.5₽ = 7550 коп
    },
  );

  it(
    "submit с catcher_amount >= penalty → всё уходит ловцу, clamp на бэкенде",
    async () => {
      // penalty_amount=30000 (300₽), catcher=50000 (500₽) — больше штрафа.
      // Бэкенд в apply_catch clamp'ит к amount (min). Этот тест только
      // проверяет что фронт отправляет как-есть (clamp — на бэкенде).
      const habit = makeHabit({
        penalty_amount: 30000,
        catcher_amount_kopecks: 50000,
      });
      renderForm(habit);

      const input = await screen.findByDisplayValue("500");
      fireEvent.change(input, { target: { value: "500" } });
      const form = input.closest("form");
      expect(form).not.toBeNull();
      fireEvent.submit(form!);

      await waitFor(() => {
        expect(mockUpdate).toHaveBeenCalledTimes(1);
      });
      const payload = mockUpdate.mock.calls[0][1];
      expect(payload.catcher_amount_kopecks).toBe(50000);
      expect(payload.penalty_amount).toBe(30000);
    },
  );
});



// ── Phase 3 v2 Task 3.8: stat_definition_id contract ─────────

// Phase 3 v2 Task 3.8: мокируем useStatDefinitions (для обеих групп тестов).
const { mockUseStatDefinitionsEdit } = vi.hoisted(() => ({
  mockUseStatDefinitionsEdit: vi.fn(),
}));

vi.mock("../../hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../hooks")>();
  // Safe default: пустой массив, loading=false. Override per-test
  // через mockUseStatDefinitionsEdit.mockReturnValue({...}).
  return {
    ...actual,
    useStatDefinitions: () => {
      const fn = mockUseStatDefinitionsEdit();
      if (fn && typeof fn === "object") {
        return fn;
      }
      return {
        data: { items: [], total: 0 },
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      };
    },
  };
});

describe("HabitEditForm — stat_definition_id (Phase 3 v2 Task 3.8)", () => {
  beforeEach(() => {
    mockUpdate.mockReset();
    mockUpdate.mockResolvedValue(undefined);
  });

  const renderForm = (habit: AdminHabit) => {
    const qc = new QueryClient();
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/habits/" + habit.id + "/edit"]}>
          <Routes>
            <Route path="/habits/:id/edit" element={
              <HabitEditForm habit={habit} loading={false} error={null} />
            } />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  };

  it("omits stat_definition_id from PATCH payload when admin doesn't touch dropdown", async () => {
    // ⚠️ BUG FIX #2: PATCH должен слать stat_definition_id ТОЛЬКО при изменении.
    // Habit имеет stale UUID (НЕ в активном каталоге); admin меняет
    // только title. Payload НЕ должен содержать stat_definition_id —
    // backend (Task 3.7 exclude_unset) оставит прежнее значение в БД
    // и НЕ вызовет _validate_stat_definition_id_exists.
    const staleUuid = "11111111-1111-1111-1111-111111111111";
    mockUseStatDefinitionsEdit.mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    const habit = makeHabit({
      id: "habit-stale",
      title: "Старое название",
      stat_definition_id: staleUuid,
    });
    mockGet.mockResolvedValue(habit);
    renderForm(habit);

    // Изменяем только title (через displayValue, т.к. FieldRow не использует htmlFor).
    const titleInput = screen.getByDisplayValue("Старое название");
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "Новое название");

    const saveButton = screen.getByRole("button", { name: /Сохранить/ });
    await userEvent.click(saveButton);

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledTimes(1);
    });
    const payload = mockUpdate.mock.calls[0][1];
    // ⚠️ Ключ stat_definition_id НЕ присутствует в payload.
    expect(payload).not.toHaveProperty("stat_definition_id");
    // title — обновлён.
    expect(payload.title).toBe("Новое название");
  });

  it("includes stat_definition_id in PATCH payload when admin changes dropdown", async () => {
    // Явный change → ключ stat_definition_id ВКЛЮЧЁН в payload.
    // "— Не выбрано —" → null → backend очищает колонку.
    const activeUuid = "22222222-2222-2222-2222-222222222222";
    mockUseStatDefinitionsEdit.mockReturnValue({
      data: {
        items: [
          { id: activeUuid, slug: "intelligence", name: "Интеллект", icon: "🧠", sort_order: 1 },
        ],
        total: 1,
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    const habit = makeHabit({
      id: "habit-active",
      title: "Test",
      stat_definition_id: activeUuid,
    });
    mockGet.mockResolvedValue(habit);
    renderForm(habit);

    // Клик на select → выбрать "— Не выбрано —" (sentinel "").
    const select = screen.getByTestId(
      "stat-definition-select",
    ) as HTMLSelectElement;
    await userEvent.selectOptions(select, "");

    const saveButton = screen.getByRole("button", { name: /Сохранить/ });
    await userEvent.click(saveButton);

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalledTimes(1);
    });
    const payload = mockUpdate.mock.calls[0][1];
    // Ключ ЕСТЬ в payload → explicit null → backend очистит колонку.
    expect(payload).toHaveProperty("stat_definition_id");
    expect(payload.stat_definition_id).toBeNull();
  });
});
