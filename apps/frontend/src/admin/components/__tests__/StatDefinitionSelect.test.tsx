// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StatDefinitionSelect } from "../StatDefinitionSelect";

// vi.hoisted factory for useStatDefinitions.
const { mockUseStatDefinitions } = vi.hoisted(() => {
  return {
    mockUseStatDefinitions: vi.fn(),
  };
});

vi.mock("../../hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../hooks")>();
  return {
    ...actual,
    useStatDefinitions: () => mockUseStatDefinitions(),
  };
});

const CANONICAL_8 = [
  { id: "sd-1", slug: "intelligence", name: "Интеллект", icon: "🧠", sort_order: 1 },
  { id: "sd-2", slug: "strength", name: "Сила", icon: "💪", sort_order: 2 },
  { id: "sd-3", slug: "endurance", name: "Выносливость", icon: "🫁", sort_order: 3 },
  { id: "sd-4", slug: "balance", name: "Баланс", icon: "🧘", sort_order: 4 },
  { id: "sd-5", slug: "energy", name: "Энергия", icon: "✨", sort_order: 5 },
  { id: "sd-6", slug: "focus", name: "Фокус", icon: "🎯", sort_order: 6 },
  { id: "sd-7", slug: "creativity", name: "Творчество", icon: "🎨", sort_order: 7 },
  { id: "sd-8", slug: "connections", name: "Связи", icon: "🤝", sort_order: 8 },
];

const wrap = (ui: React.ReactElement) => (
  <QueryClientProvider client={new QueryClient()}>
    {ui}
  </QueryClientProvider>
);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("StatDefinitionSelect", () => {
  it("renders loading state when catalog query is loading", () => {
    mockUseStatDefinitions.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    });
    render(
      wrap(<StatDefinitionSelect value={null} onChange={vi.fn()} required />),
    );
    expect(screen.getByRole("status")).toHaveTextContent(/Загружаю/);
    expect(screen.queryByTestId("stat-definition-select")).not.toBeInTheDocument();
  });

  it("renders options sorted by sort_order ASC when catalog loaded", () => {
    mockUseStatDefinitions.mockReturnValue({
      data: { items: CANONICAL_8, total: 8 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    render(
      wrap(<StatDefinitionSelect value={null} onChange={vi.fn()} required />),
    );
    const select = screen.getByTestId("stat-definition-select") as HTMLSelectElement;
    const optionTexts = Array.from(select.options).map((o) => o.text);
    // sort_order ASC: 🧠 Интеллект, 💪 Сила, ..., 🤝 Связи.
    expect(optionTexts[0]).toMatch(/🧠/);
    expect(optionTexts[1]).toMatch(/💪/);
    expect(optionTexts[7]).toMatch(/🤝/);
    // Required=true → НЕТ "Не выбрано" опции.
    expect(optionTexts.some((t) => /Не выбрано/.test(t))).toBe(false);
  });

  it("renders '— Не выбрано —' option when required=false (Edit mode)", () => {
    mockUseStatDefinitions.mockReturnValue({
      data: { items: CANONICAL_8, total: 8 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    render(
      wrap(
        <StatDefinitionSelect
          value={null}
          onChange={vi.fn()}
          required={false}
        />,
      ),
    );
    const select = screen.getByTestId("stat-definition-select") as HTMLSelectElement;
    const optionTexts = Array.from(select.options).map((o) => o.text);
    expect(optionTexts[0]).toMatch(/Не выбрано/);
  });

  it("shows deactivated-stat warning when habit.stat_definition_id NOT in active catalog", () => {
    // ⚠️ Edge case (Task 3.8): 8 canonical, НИ ОДИН не совпадает с
    // habit.stat_definition_id="stale-uuid" → banner показывается,
    // select НЕ disabled (admin может выбрать другую / очистить).
    mockUseStatDefinitions.mockReturnValue({
      data: { items: CANONICAL_8, total: 8 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    const onChange = vi.fn();
    render(
      wrap(
        <StatDefinitionSelect
          value="stale-uuid-not-in-catalog"
          onChange={onChange}
          required={false}
          deactivatedIdWarning={true}
        />,
      ),
    );
    const warning = screen.getByTestId("deactivated-warning");
    expect(warning).toBeInTheDocument();
    expect(warning.textContent).toMatch(/не найдена/);
    expect(warning.textContent).toMatch(/деактивирована/);
    // Select НЕ disabled — admin может менять выбор.
    const select = screen.getByTestId("stat-definition-select") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
  });
});
