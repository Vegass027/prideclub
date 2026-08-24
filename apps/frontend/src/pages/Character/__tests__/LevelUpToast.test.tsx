// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { LevelUpToast } from "../LevelUpToast";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("LevelUpToast (Phase 3 v2 Task 3.9)", () => {
  it("renders nothing when visible=false", () => {
    const { container } = render(
      <LevelUpToast
        visible={false}
        previousName="На старте"
        newName="В потоке"
        onDone={() => undefined}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders toast with 'Новый статус' text + auto-hides через 4s (onDone)", () => {
    const onDone = vi.fn();
    render(
      <LevelUpToast
        visible
        previousName="На старте"
        newName="В потоке"
        onDone={onDone}
      />,
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      screen.getByText(/🎉 Новый статус: «В потоке»/),
    ).toBeInTheDocument();

    // До 4s — onDone не вызван.
    act(() => {
      vi.advanceTimersByTime(3999);
    });
    expect(onDone).not.toHaveBeenCalled();

    // После 4s — onDone вызван.
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(onDone).toHaveBeenCalledTimes(1);
  });
});