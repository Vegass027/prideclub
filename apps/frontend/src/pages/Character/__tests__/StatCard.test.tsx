// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatCard } from "../StatCard";
import type { CharacterStatOut } from "@/shared/types";

const activeStat: CharacterStatOut = {
  stat_definition_id: "11111111-1111-1111-1111-111111111111",
  stat_slug: "strength",
  stat_name: "Сила",
  stat_icon: "💪",
  value: 42,
  is_frozen: false,
  frozen_reason_text: null,
  last_checkin_at: "2026-08-22T10:00:00Z",
};

const frozenStat: CharacterStatOut = {
  ...activeStat,
  stat_slug: "intelligence",
  stat_name: "Интеллект",
  stat_icon: "🧠",
  value: 12,
  is_frozen: true,
  frozen_reason_text: "30 дней без чек-ина",
  last_checkin_at: "2026-07-15T10:00:00Z",
};

describe("StatCard (Phase 3 v2 Task 3.9)", () => {
  it("renders active stat normally (icon + name + value + last checkin)", () => {
    render(<StatCard stat={activeStat} />);
    expect(screen.getByText("Сила")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText(/Последний чек-ин/)).toBeInTheDocument();
    expect(screen.queryByText(/Заморожена/)).not.toBeInTheDocument();
    const root = document.querySelector("article");
    expect(root?.getAttribute("data-frozen")).toBe("false");
  });

  it("renders frozen stat with ❄ badge + opacity + frozen_reason_text", () => {
    render(<StatCard stat={frozenStat} />);
    expect(screen.getByText("Интеллект")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(
      screen.getByText(/❄ Заморожена: «30 дней без чек-ина»/),
    ).toBeInTheDocument();
    const root = document.querySelector("article");
    expect(root?.getAttribute("data-frozen")).toBe("true");
    expect(root?.className).toMatch(/opacity-60/);
  });
});