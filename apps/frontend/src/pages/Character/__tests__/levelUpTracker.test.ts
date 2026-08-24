// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useLevelUpStatus } from "@/shared/hooks/levelUpTracker";

// Мокаем haptic чтобы убедиться что он НЕ вызывается при downgrade.
vi.mock("@/shared/telegram/tma", () => ({
  hapticImpact: vi.fn(),
}));

import { hapticImpact } from "@/shared/telegram/tma";

beforeEach(() => {
  vi.mocked(hapticImpact).mockClear();
});

describe("useLevelUpStatus (Phase 3 v2 Task 3.9 — direction-aware)", () => {
  it("first mount: justLeveledUp=false (calibration happens silently)", () => {
    const { result } = renderHook(() => useLevelUpStatus("В потоке", 50));
    expect(result.current.justLeveledUp).toBe(false);
    expect(result.current.previousName).toBe("В потоке");
    expect(result.current.previousTotal).toBe(50);
    expect(hapticImpact).not.toHaveBeenCalled();
  });

  it("same name on re-render: justLeveledUp=false (no change)", () => {
    const { result, rerender } = renderHook(
      ({ name, total }: { name: string; total: number }) =>
        useLevelUpStatus(name, total),
      { initialProps: { name: "В потоке", total: 50 } },
    );
    // Acknowledge initial.
    act(() => result.current.acknowledgeLevelUp());
    // Re-render с теми же значениями.
    rerender({ name: "В потоке", total: 50 });
    expect(result.current.justLeveledUp).toBe(false);
  });

  it("level UP (name changes AND total INCREASES): justLeveledUp=true + haptic", () => {
    const { result, rerender } = renderHook(
      ({ name, total }: { name: string; total: number }) =>
        useLevelUpStatus(name, total),
      { initialProps: { name: "На старте", total: 50 } },
    );
    act(() => result.current.acknowledgeLevelUp());
    expect(hapticImpact).not.toHaveBeenCalled();

    // Повышение: name change + total увеличился.
    rerender({ name: "В потоке", total: 120 });
    expect(result.current.justLeveledUp).toBe(true);
    expect(hapticImpact).toHaveBeenCalledWith("medium");
    expect(hapticImpact).toHaveBeenCalledTimes(1);
  });

  // ⚠️ CRITICAL: тест на downgrade (Task 3.9 план, blocker Dmitry).
  // Проверяет: ОДНОВРЕМЕННО (a) total уменьшился AND (b) name поменялся
  // → justLeveledUp=false, НЕ haptic. Это сценарий «поймали нарушителя,
  // статус понизился с «На волне» до «В потоке»».
  it("level DOWN (name changes AND total DECREASES): justLeveledUp=false, no haptic", () => {
    const { result, rerender } = renderHook(
      ({ name, total }: { name: string; total: number }) =>
        useLevelUpStatus(name, total),
      { initialProps: { name: "На волне", total: 150 } },
    );
    act(() => result.current.acknowledgeLevelUp());

    // Downgrade: name change + total уменьшился.
    rerender({ name: "В потоке", total: 80 });

    // ⚠️ Оба условия downgrade выполнены: justLeveledUp=false.
    expect(result.current.justLeveledUp).toBe(false);
    expect(result.current.previousName).toBe("На волне");
    expect(result.current.previousTotal).toBe(150);
    // Haptic НЕ вызывается при downgrade.
    expect(hapticImpact).not.toHaveBeenCalled();
  });
});