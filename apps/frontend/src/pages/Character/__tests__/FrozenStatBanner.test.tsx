// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FrozenStatBanner } from "../FrozenStatBanner";
import type { CharacterStatOut } from "@/shared/types";

const active: CharacterStatOut = {
  stat_definition_id: "11111111-1111-1111-1111-111111111111",
  stat_slug: "strength",
  stat_name: "Сила",
  stat_icon: "💪",
  value: 10,
  is_frozen: false,
  frozen_reason_text: null,
  last_checkin_at: null,
};

const frozen1: CharacterStatOut = {
  ...active,
  stat_definition_id: "22222222-2222-2222-2222-222222222222",
  stat_slug: "intelligence",
  stat_name: "Интеллект",
  stat_icon: "🧠",
  is_frozen: true,
  frozen_reason_text: "30 дней без чек-ина",
  last_checkin_at: "2026-07-15T10:00:00Z",
};

describe("FrozenStatBanner (Phase 3 v2 Task 3.9)", () => {
  it("returns null when no stats are frozen", () => {
    const { container } = render(<FrozenStatBanner stats={[active]} />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders banner with frozen stat details when at least one is frozen", () => {
    render(<FrozenStatBanner stats={[active, frozen1]} />);
    const banner = screen.getByRole("alert");
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toMatch(/1.*заморожена/i);
    // Frozen stat — в баннере.
    expect(screen.getByText(/Интеллект/)).toBeInTheDocument();
    expect(screen.getByText(/30 дней без чек-ина/)).toBeInTheDocument();
    // ⚠️ Negative assertion: активные stats исключены из баннера (by design).
    // Это regression-guard на случай если будущий рефактор добавит
    // показ всех stats в баннер и сломает смысл (баннер только о замороженных).
    expect(screen.queryByText(/Сила/)).not.toBeInTheDocument();
  });
});