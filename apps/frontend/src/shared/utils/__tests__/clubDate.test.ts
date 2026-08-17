import { describe, expect, it, vi } from "vitest";
import { calendarDaysBetween, clubTodayInTz } from "../clubDate";

describe("clubTodayInTz", () => {
  it("Europe/Moscow в 12:00 UTC = тот же день (15:00 МСК)", () => {
    // 12:00 UTC = 15:00 Europe/Moscow (UTC+3). Тот же день.
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(clubTodayInTz("Europe/Moscow")).toBe("2026-08-17");
  });

  it("Asia/Tokyo в 12:00 UTC = тот же день (21:00 Tokyo)", () => {
    // 12:00 UTC = 21:00 Asia/Tokyo (UTC+9). Тот же день.
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    expect(clubTodayInTz("Asia/Tokyo")).toBe("2026-08-17");
  });

  it("Asia/Tokyo в 18:00 UTC = следующий день (03:00 Tokyo)", () => {
    // 18:00 UTC = 03:00 Asia/Tokyo (UTC+9) следующего дня.
    vi.setSystemTime(new Date("2026-08-17T18:00:00Z"));
    expect(clubTodayInTz("Asia/Tokyo")).toBe("2026-08-18");
  });

  it("Europe/Moscow в 18:00 UTC = тот же день (21:00 МСК)", () => {
    // 18:00 UTC = 21:00 Europe/Moscow (UTC+3). Полночь в Москве ещё НЕ наступила.
    vi.setSystemTime(new Date("2026-08-17T18:00:00Z"));
    expect(clubTodayInTz("Europe/Moscow")).toBe("2026-08-17");
  });

  it("Europe/Moscow в 22:00 UTC = следующий день (01:00 МСК)", () => {
    // 22:00 UTC = 01:00 Europe/Moscow (UTC+3) следующего дня.
    vi.setSystemTime(new Date("2026-08-17T22:00:00Z"));
    expect(clubTodayInTz("Europe/Moscow")).toBe("2026-08-18");
  });
});

describe("calendarDaysBetween", () => {
  it("одинаковые даты → 0", () => {
    expect(calendarDaysBetween("2026-08-17", "2026-08-17")).toBe(0);
  });

  it("toIso через день → 1", () => {
    expect(calendarDaysBetween("2026-08-17", "2026-08-18")).toBe(1);
  });

  it("toIso за 2 дня → 2", () => {
    expect(calendarDaysBetween("2026-08-17", "2026-08-19")).toBe(2);
  });

  it("toIso вчера → -1", () => {
    expect(calendarDaysBetween("2026-08-17", "2026-08-16")).toBe(-1);
  });

  it("toIso месяц назад → -30", () => {
    expect(calendarDaysBetween("2026-08-17", "2026-07-18")).toBe(-30);
  });
});