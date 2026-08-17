import { describe, expect, it, vi } from "vitest";
import { computeSubState } from "../subscriptionState";

const TZ = "Europe/Moscow";

describe("computeSubState", () => {
  it("null subscription_until → null (без бейджа)", () => {
    expect(computeSubState(null, TZ)).toBeNull();
  });

  it("undefined subscription_until → null (без бейджа)", () => {
    expect(computeSubState(undefined, TZ)).toBeNull();
  });

  it("calendarDiff=2, daysLeft=3 → ok (без бейджа, достаточно времени)", () => {
    // today=2026-08-17, subUntil=2026-08-19 → diff=2, daysLeft=3 → ok.
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState("2026-08-19", TZ)).toEqual({ kind: "ok" });
  });

  it("calendarDiff=1, daysLeft=2 → soon 'через 2 дня'", () => {
    // today=2026-08-17, subUntil=2026-08-18 → diff=1, daysLeft=2 → soon.
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState("2026-08-18", TZ)).toEqual({ kind: "soon", daysLeft: 2 });
  });

  it("calendarDiff=0, daysLeft=1 → soon 'через 1 день' (today — последний день)", () => {
    // КРИТИЧНО: бэкенд пускает чек-ин когда subscription_until == club_date
    // (Q2: без grace period). Фронт НЕ должен показывать expired в этот день.
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState("2026-08-17", TZ)).toEqual({ kind: "soon", daysLeft: 1 });
  });

  it("calendarDiff=-1 → expired", () => {
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState("2026-08-16", TZ)).toEqual({ kind: "expired" });
  });

  it("calendarDiff=3, daysLeft=4 → ok (3+ дня буфер)", () => {
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState("2026-08-20", TZ)).toEqual({ kind: "ok" });
  });

  it("calendarDiff=-30 → expired (месяц назад)", () => {
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState("2026-07-18", TZ)).toEqual({ kind: "expired" });
  });

  it("Date объект нормализуется в ISO-дату через toISOString().slice(0,10)", () => {
    // today=2026-08-17, subUntil=Date('2026-08-18T...') → нормализуется.
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(computeSubState(new Date("2026-08-18T00:00:00Z"), TZ)).toEqual({
      kind: "soon",
      daysLeft: 2,
    });
  });

  it("TZ-чувствительность: один и тот же момент, разные TZ — разные состояния", () => {
    // Pravki-subscription-2026-08-17 §TZ-edge: 18:00 UTC — окно, где
    // Moscow ещё в старом дне, а Tokyo уже в новом. Тест проверяет,
    // что фронт учитывает TZ клуба (а не UTC).
    vi.setSystemTime(new Date("2026-08-17T18:00:00Z"));

    // Tokyo club_today = 2026-08-18. subUntil=2026-08-18 → diff=0, daysLeft=1 → soon.
    expect(computeSubState("2026-08-18", "Asia/Tokyo")).toEqual({
      kind: "soon",
      daysLeft: 1,
    });

    // Moscow club_today = 2026-08-17. Тот же subUntil=2026-08-18 → diff=1, daysLeft=2 → soon.
    expect(computeSubState("2026-08-18", "Europe/Moscow")).toEqual({
      kind: "soon",
      daysLeft: 2,
    });
  });
});