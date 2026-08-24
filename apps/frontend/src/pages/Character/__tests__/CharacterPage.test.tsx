// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { mockCharacterGet, mockLevelUpStatus } = vi.hoisted(() => ({
  mockCharacterGet: vi.fn(),
  mockLevelUpStatus: {
    previousName: null as string | null,
    previousTotal: null as number | null,
    justLeveledUp: false,
    acknowledgeLevelUp: vi.fn(),
  },
}));

vi.mock("@/shared/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/api")>();
  return {
    ...actual,
    characterApi: {
      get: mockCharacterGet,
    },
  };
});

vi.mock("@/shared/hooks/levelUpTracker", () => ({
  useLevelUpStatus: () => mockLevelUpStatus,
}));

import { CharacterPage } from "../CharacterPage";
import type { CharacterResponse } from "@/shared/types";

const fullCharacter: CharacterResponse = {
  total_value: 250,
  status: {
    name: "На волне",
    icon: "⚡",
    next_threshold: 500,
    next_status: "В огне",
  },
  stats: [
    {
      stat_definition_id: "11111111-1111-1111-1111-111111111111",
      stat_slug: "strength",
      stat_name: "Сила",
      stat_icon: "💪",
      value: 120,
      is_frozen: false,
      frozen_reason_text: null,
      last_checkin_at: "2026-08-22T10:00:00Z",
    },
    {
      stat_definition_id: "22222222-2222-2222-2222-222222222222",
      stat_slug: "intelligence",
      stat_name: "Интеллект",
      stat_icon: "🧠",
      value: 12,
      is_frozen: true,
      frozen_reason_text: "30 дней без чек-ина",
      last_checkin_at: "2026-07-15T10:00:00Z",
    },
  ],
};

const renderPage = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/character"]}>
        <CharacterPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  mockCharacterGet.mockReset();
  // Сбрасываем мок уровня на дефолт.
  mockLevelUpStatus.previousName = null;
  mockLevelUpStatus.previousTotal = null;
  mockLevelUpStatus.justLeveledUp = false;
  mockLevelUpStatus.acknowledgeLevelUp = vi.fn();
});

describe("CharacterPage (Phase 3 v2 Task 3.9)", () => {
  it("показывает Skeleton пока useQuery loading", () => {
    mockCharacterGet.mockReturnValue(new Promise(() => undefined)); // никогда не резолвится
    renderPage();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("рендерит пустое состояние когда useQuery вернул stats=[]", async () => {
    mockCharacterGet.mockResolvedValue({
      total_value: 0,
      status: {
        name: "На старте",
        icon: "🐣",
        next_threshold: 50,
        next_status: "В потоке",
      },
      stats: [],
    });
    renderPage();
    expect(await screen.findByText(/«На старте»/)).toBeInTheDocument();
    expect(await screen.findByText(/Пока пусто/)).toBeInTheDocument();
  });

  it("рендерит mixed active+frozen stats: FrozenStatBanner + StatCard ×2", async () => {
    mockCharacterGet.mockResolvedValue(fullCharacter);
    renderPage();
    // StatusBadge: «На волне», 250 ед.
    expect(await screen.findByText(/«На волне»/)).toBeInTheDocument();
    expect(await screen.findByText("250 ед.")).toBeInTheDocument();
    // FrozenStatBanner: 1 заморожен (Интеллект)
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    // ⚠️ Frozen stat появляется в ДВУХ местах (by design):
    // FrozenStatBanner (<strong>) + StatCard (<h3>). Проверяем количество
    // явно — это regression-guard на случай если баннер или карточка
    // исчезнут в будущем.
    const intelMatches = screen.getAllByText(/Интеллект/);
    expect(intelMatches).toHaveLength(2);
    // Та же история для frozen_reason_text — баннер показывает его с датой,
    // StatCard показывает его без даты. Два места.
    const reasonMatches = screen.getAllByText(/30 дней без чек-ина/);
    expect(reasonMatches).toHaveLength(2);
    // StatCard — Сила
    expect(screen.getByText("Сила")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
    // Frozen сначала ушёл в конец сортировки (active перед frozen)
    const cards = document.querySelectorAll("article[data-frozen]");
    expect(cards.length).toBe(2);
    expect(cards[0].getAttribute("data-frozen")).toBe("false");
    expect(cards[1].getAttribute("data-frozen")).toBe("true");
  });
});