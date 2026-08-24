// @vitest-environment jsdom
// Phase 3 v2: тесты для StatDefinitionSelect — custom dropdown (Task 3.8 fix).
//
// Покрываем:
// 1. loading state — "Загружаю каталог…"
// 2. error state — banner + Retry работает
// 3. open dropdown + click option вызывает onChange
// 4. close on outside click
// 5. close on Escape
// 6. required=true скрывает "— Не выбрано —"

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { StatDefinitionSelect } from "../StatDefinitionSelect";
import type { AdminStatDefinition } from "@/admin/api";

const STATS: AdminStatDefinition[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    slug: "intelligence",
    name: "Интеллект",
    icon: "🧠",
    sort_order: 1,
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    slug: "strength",
    name: "Сила",
    icon: "💪",
    sort_order: 2,
  },
];

const { mockUseStatDefinitionsSelect } = vi.hoisted(() => ({
  mockUseStatDefinitionsSelect: vi.fn(),
}));

vi.mock("../../hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../hooks")>();
  return {
    ...actual,
    useStatDefinitions: () => {
      const fn = mockUseStatDefinitionsSelect();
      if (fn && typeof fn === "object") {
        return fn;
      }
      // Safe default — пустой каталог.
      return {
        data: { items: [], total: 0 },
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      };
    },
  };
});

beforeEach(() => {
  mockUseStatDefinitionsSelect.mockReset();
  mockUseStatDefinitionsSelect.mockReturnValue({
    data: { items: STATS, total: STATS.length },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
});

describe("StatDefinitionSelect (Phase 3 v2 — custom dropdown)", () => {
  it("loading: показывает 'Загружаю каталог характеристик…'", () => {
    mockUseStatDefinitionsSelect.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    });
    render(<StatDefinitionSelect value={null} onChange={() => undefined} />);
    expect(screen.getByText(/Загружаю каталог характеристик…/)).toBeInTheDocument();
    // Trigger button НЕ отрисован в loading state.
    expect(screen.queryByTestId("stat-definition-select")).toBeNull();
  });

  it("error: показывает баннер + Retry вызывает refetch", async () => {
    const refetch = vi.fn();
    mockUseStatDefinitionsSelect.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    });
    const user = userEvent.setup();
    render(<StatDefinitionSelect value={null} onChange={() => undefined} />);
    expect(
      screen.getByText(/Не удалось загрузить каталог характеристик\./),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Retry/ }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("open + select option: клик по Интеллект вызывает onChange(intelUuid) и закрывает listbox", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <StatDefinitionSelect value={null} onChange={onChange} required />,
    );
    // Trigger показывает sentinel-текст.
    const trigger = screen.getByTestId("stat-definition-select");
    expect(trigger).toHaveTextContent("Выберите характеристику…");
    // aria-expanded=false initially.
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    // Open.
    await user.click(trigger);
    await waitFor(() => {
      expect(
        screen.getByTestId("stat-definition-listbox"),
      ).toBeInTheDocument();
    });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    // Required=true → "— Не выбрано —" НЕ показывается.
    expect(screen.queryByTestId("stat-definition-option-none")).toBeNull();
    // Клик по Интеллект.
    await user.click(
      screen.getByTestId(`stat-definition-option-${STATS[0].id}`),
    );
    expect(onChange).toHaveBeenCalledWith(STATS[0].id);
    // Listbox закрылся.
    await waitFor(() => {
      expect(
        screen.queryByTestId("stat-definition-listbox"),
      ).not.toBeInTheDocument();
    });
  });

  it("required=false: показывает '— Не выбрано —' → onChange(null)", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <StatDefinitionSelect
        value={STATS[0].id}
        onChange={onChange}
        required={false}
      />,
    );
    const trigger = screen.getByTestId("stat-definition-select");
    expect(trigger).toHaveTextContent("🧠 Интеллект"); // selected label
    await user.click(trigger);
    const noneOption = await screen.findByTestId(
      "stat-definition-option-none",
    );
    expect(noneOption).toBeInTheDocument();
    await user.click(noneOption);
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("outside click: закрывает listbox", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <div>
        <button data-testid="outside">outside</button>
        <StatDefinitionSelect value={null} onChange={onChange} required />
      </div>,
    );
    const trigger = screen.getByTestId("stat-definition-select");
    await user.click(trigger);
    expect(
      screen.getByTestId("stat-definition-listbox"),
    ).toBeInTheDocument();
    // Клик снаружи.
    await user.click(screen.getByTestId("outside"));
    await waitFor(() => {
      expect(
        screen.queryByTestId("stat-definition-listbox"),
      ).not.toBeInTheDocument();
    });
    expect(onChange).not.toHaveBeenCalled();
  });

  it("Escape: закрывает listbox", async () => {
    const user = userEvent.setup();
    render(<StatDefinitionSelect value={null} onChange={() => undefined} />);
    const trigger = screen.getByTestId("stat-definition-select");
    await user.click(trigger);
    expect(
      screen.getByTestId("stat-definition-listbox"),
    ).toBeInTheDocument();
    // fireEvent.keyDown с key=Escape (user.keyboard не работает стабильно
    // с document-level listener в jsdom — используем fireEvent).
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => {
      expect(
        screen.queryByTestId("stat-definition-listbox"),
      ).not.toBeInTheDocument();
    });
  });

  it("render: trigger label показывает выбранный icon + name", () => {
    render(
      <StatDefinitionSelect
        value={STATS[1].id}
        onChange={() => undefined}
      />,
    );
    const trigger = screen.getByTestId("stat-definition-select");
    expect(trigger).toHaveTextContent("💪 Сила");
  });

  it("label: НЕ рендерится внутри компонента (parent FieldRow отвечает за label)", () => {
    render(
      <StatDefinitionSelect value={null} onChange={() => undefined} />,
    );
    // Никакого "Характеристика" текста в trigger или listbox — label
    // рендерится только родителем через FieldRow.
    const trigger = screen.getByTestId("stat-definition-select");
    expect(trigger.textContent).not.toContain("Характеристика");
  });
});