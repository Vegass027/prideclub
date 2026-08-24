// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "../StatusBadge";
import type { CharacterStatusInfo } from "@/shared/types";

const midStatus: CharacterStatusInfo = {
  name: "В потоке",
  icon: "🌊",
  next_threshold: 100,
  next_status: "На волне",
};

const maxStatus: CharacterStatusInfo = {
  name: "Режим зверя",
  icon: "🐺",
  next_threshold: null,
  next_status: null,
};

describe("StatusBadge (Phase 3 v2 Task 3.9)", () => {
  it("renders progress bar when next_threshold and next_status are present", () => {
    render(<StatusBadge status={midStatus} total={30} />);
    expect(screen.getByText("«В потоке»")).toBeInTheDocument();
    expect(screen.getByText("30 ед.")).toBeInTheDocument();
    const progressbar = screen.getByRole("progressbar");
    expect(progressbar).toHaveAttribute("aria-valuenow", "30");
    expect(progressbar).toHaveAttribute("aria-valuemin", "0");
    expect(progressbar).toHaveAttribute("aria-valuemax", "100");
    // +100 -30 = 70 до следующего
    expect(screen.getByText("+70 до «На волне»")).toBeInTheDocument();
  });

  it("renders 'Максимальная ступень' без progress bar when next_threshold=null", () => {
    render(<StatusBadge status={maxStatus} total={500} />);
    expect(screen.getByText("«Режим зверя»")).toBeInTheDocument();
    expect(screen.getByText("500 ед.")).toBeInTheDocument();
    expect(screen.getByText(/Максимальная ступень/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("clamps progress bar at 100% when total exceeds threshold (defensive)", () => {
    // Defensive: backend гарантирует этого не будет, но UI должен быть robust.
    render(<StatusBadge status={midStatus} total={150} />);
    const progressbar = screen.getByRole("progressbar");
    expect(progressbar).toHaveAttribute("aria-valuenow", "100");
  });
});