import { describe, expect, it } from "vitest";
import { CheckinRejectCode } from "@/shared/types/checkinReject";
import { checkinRejectText } from "../checkinReject";

describe("checkinRejectText mapper", () => {
  describe("WINDOW_CLOSED", () => {
    it("uses window_start/end from context", () => {
      const text = checkinRejectText(CheckinRejectCode.WINDOW_CLOSED, {
        window_start: "06:00",
        window_end: "12:00",
      });
      expect(text).toContain("06:00");
      expect(text).toContain("12:00");
      expect(text).toContain("окно");
    });

    it("falls back to '?' when window times missing", () => {
      const text = checkinRejectText(CheckinRejectCode.WINDOW_CLOSED);
      expect(text).toContain("?");
      expect(text).toContain("окно");
    });
  });

  describe("MEMBERSHIP_PAUSED", () => {
    it("contains 'паузе' and recovery hint 'пополни депозит'", () => {
      const text = checkinRejectText(CheckinRejectCode.MEMBERSHIP_PAUSED);
      expect(text).toContain("паузе");
      expect(text.toLowerCase()).toContain("депозит");
    });
  });

  describe("MEMBERSHIP_LEFT", () => {
    it("contains 'больше не участник' and recovery hint 'вступи'", () => {
      const text = checkinRejectText(CheckinRejectCode.MEMBERSHIP_LEFT);
      expect(text).toContain("больше не участник");
      expect(text.toLowerCase()).toContain("вступи");
    });
  });

  describe("FORWARDED", () => {
    it("contains 'пересланные видео' and action hint 'своё'", () => {
      const text = checkinRejectText(CheckinRejectCode.FORWARDED);
      expect(text.toLowerCase()).toContain("пересланные видео");
      expect(text.toLowerCase()).toContain("своё");
    });
  });

  describe("WRONG_TOPIC", () => {
    it("contains 'топик'", () => {
      const text = checkinRejectText(CheckinRejectCode.WRONG_TOPIC);
      expect(text.toLowerCase()).toContain("топик");
    });
  });

  describe("ALREADY_CAUGHT", () => {
    it("contains 'поймали'", () => {
      const text = checkinRejectText(CheckinRejectCode.ALREADY_CAUGHT);
      expect(text.toLowerCase()).toContain("поймали");
    });
  });

  describe("ALREADY_CHECKED_IN", () => {
    it("contains 'уже отметился'", () => {
      const text = checkinRejectText(CheckinRejectCode.ALREADY_CHECKED_IN);
      expect(text.toLowerCase()).toContain("уже отметился");
    });
  });

  describe("JOINED_LATE", () => {
    it("uses window_start/end from context", () => {
      const text = checkinRejectText(CheckinRejectCode.JOINED_LATE, {
        window_start: "06:00",
        window_end: "12:00",
      });
      expect(text).toContain("06:00");
      expect(text).toContain("12:00");
    });
  });

  describe("TOO_SHORT", () => {
    it("contains 'кружок' and '3 секунды'", () => {
      const text = checkinRejectText(CheckinRejectCode.TOO_SHORT);
      expect(text.toLowerCase()).toContain("кружок");
      expect(text).toContain("3");
    });
  });

  describe("fallback", () => {
    it("returns REJECT_UNKNOWN for unknown code", () => {
      const text = checkinRejectText("some_weird_code");
      expect(text.toLowerCase()).toContain("не получилось");
    });

    it("returns REJECT_UNKNOWN for null code", () => {
      const text = checkinRejectText(null);
      expect(text.toLowerCase()).toContain("не получилось");
    });

    it("returns REJECT_UNKNOWN for undefined code", () => {
      const text = checkinRejectText(undefined);
      expect(text.toLowerCase()).toContain("не получилось");
    });
  });

  describe("structural codes", () => {
    it("HABIT_NOT_FOUND has actionable message", () => {
      const text = checkinRejectText(CheckinRejectCode.HABIT_NOT_FOUND);
      expect(text.toLowerCase()).toContain("клуб");
    });

    it("MEMBERSHIP_NOT_FOUND has actionable message", () => {
      const text = checkinRejectText(CheckinRejectCode.MEMBERSHIP_NOT_FOUND);
      expect(text.toLowerCase()).toContain("тебя нет");
    });

    it("MEMBERSHIP_NOT_ACTIVE пока legacy fallback", () => {
      const text = checkinRejectText(CheckinRejectCode.MEMBERSHIP_NOT_ACTIVE);
      expect(text.toLowerCase()).toContain("не получилось");
    });
  });
});
