// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { mockStreaks, mockCatches, mockShame, mockStat } = vi.hoisted(() => ({
  mockStreaks: vi.fn(),
  mockCatches: vi.fn(),
  mockShame: vi.fn(),
  mockStat: vi.fn(),
}));

vi.mock("@/shared/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/api")>();
  return {
    ...actual,
    leaderboardApi: {
      streaks: mockStreaks,
      catchers: mockCatches,
      shame: mockShame,
      stat: mockStat,
    },
  };
});

import { LeaderboardPage } from "../LeaderboardPage";

const renderPage = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/habits/h1/leaderboard"]}>
        <Routes>
          <Route
            path="/habits/:habitId/leaderboard"
            element={<LeaderboardPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  mockStreaks.mockReset().mockResolvedValue({ items: [], total: null });
  mockCatches.mockReset().mockResolvedValue({ items: [], total: null });
  mockShame.mockReset().mockResolvedValue({ items: [], total: null });
  mockStat.mockReset().mockResolvedValue({ items: [], total: null });
});

describe("LeaderboardPage — stat tab (Phase 3 v2 Task 3.9)", () => {
  it("рендерит 4 таба включая «Характеристика»; stat tab показывает empty state для пустого клуба", async () => {
    const user = userEvent.setup();
    renderPage();

    // 4 таба видны сразу.
    expect(screen.getByRole("tab", { name: /Серии/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Охотники/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Лентяи/i })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /Характеристика/i }),
    ).toBeInTheDocument();

    // Кликаем на stat tab — empty state от backend.
    await user.click(screen.getByRole("tab", { name: /Характеристика/i }));
    await waitFor(() => {
      expect(mockStat).toHaveBeenCalledWith("h1");
    });
    expect(
      await screen.findByText(/Характеристика не активирована/),
    ).toBeInTheDocument();
  });
});