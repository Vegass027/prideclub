import { describe, expect, it } from "vitest";
import { CheckinRejectCode } from "../checkinReject";

describe("CheckinRejectCode mirror", () => {
  // Документированный source-of-truth list. Если руками добавили
  // значение в backend enum — добавляем и здесь. Если тест ниже
  // упал — значит drift с backend, синхронизируйте вручную.
  it("must match backend values", () => {
    expect(CheckinRejectCode.HABIT_NOT_FOUND).toBe("habit_not_found");
    expect(CheckinRejectCode.MEMBERSHIP_NOT_FOUND).toBe("membership_not_found");
    expect(CheckinRejectCode.MEMBERSHIP_NOT_ACTIVE).toBe("membership_not_active");
    expect(CheckinRejectCode.MEMBERSHIP_PAUSED).toBe("membership_paused");
    expect(CheckinRejectCode.MEMBERSHIP_LEFT).toBe("membership_left");
    expect(CheckinRejectCode.WINDOW_CLOSED).toBe("checkin_window_closed");
    expect(CheckinRejectCode.WRONG_TOPIC).toBe("not_checkin_topic");
    expect(CheckinRejectCode.FORWARDED).toBe("forwarded");
    expect(CheckinRejectCode.ALREADY_CAUGHT).toBe("caught_today");
    expect(CheckinRejectCode.ALREADY_CHECKED_IN).toBe("checkin_already_exists");
    expect(CheckinRejectCode.JOINED_LATE).toBe("joined_late");
    expect(CheckinRejectCode.WRONG_TYPE).toBe("wrong_type");
    expect(CheckinRejectCode.TOO_SHORT).toBe("too_short");
    expect(CheckinRejectCode.STALE_MESSAGE).toBe("stale_message");
    expect(CheckinRejectCode.EMPTY_TEXT).toBe("empty");
  });

  it("has no duplicate values", () => {
    const values = Object.values(CheckinRejectCode);
    expect(new Set(values).size).toBe(values.length);
  });
});
