import { describe, expect, it } from "vitest";

import {
  DEFAULT_TOPUP_PRESETS_KOPECKS,
  missingKopecks,
  pickPresetToCover,
} from "../topupPresets";

describe("pickPresetToCover", () => {
  it("возвращает наименьший пресет, который покрывает required", () => {
    // required=320₽ = 32000 копеек → подсвечиваем 500₽ (50000), не 250₽ (25000).
    expect(pickPresetToCover(32_000, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBe(50_000);
    // required=750₽ ровно — подсвечиваем 750₽.
    expect(pickPresetToCover(75_000, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBe(75_000);
    // required=200₽ — подсвечиваем 250₽.
    expect(pickPresetToCover(20_000, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBe(25_000);
    // required=1100₽ — нет пресета >= 110000, возвращаем null → UI "своя сумма".
    expect(pickPresetToCover(110_000, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBeNull();
  });

  it("0 или отрицательный required → null (нет смысла пополнять)", () => {
    expect(pickPresetToCover(0, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBeNull();
    expect(pickPresetToCover(-100, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBeNull();
  });

  it("required > max preset → null", () => {
    expect(pickPresetToCover(200_000, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBeNull();
  });

  it("маленький required — подсвечиваем первый пресет", () => {
    expect(pickPresetToCover(1, DEFAULT_TOPUP_PRESETS_KOPECKS)).toBe(25_000);
  });

  it("с пустым списком пресетов возвращает null", () => {
    expect(pickPresetToCover(100, [])).toBeNull();
  });
});

describe("missingKopecks", () => {
  it("возвращает разницу если deposit < required", () => {
    expect(missingKopecks(50_000, 32_000)).toBe(18_000);
  });

  it("возвращает 0 если deposit >= required", () => {
    expect(missingKopecks(50_000, 50_000)).toBe(0);
    expect(missingKopecks(50_000, 75_000)).toBe(0);
  });

  it("никогда не возвращает отрицательное", () => {
    expect(missingKopecks(10_000, 50_000)).toBe(0);
  });
});

describe("DEFAULT_TOPUP_PRESETS_KOPECKS", () => {
  it("содержит ровно 4 пресета: 250/500/750/1000 ₽", () => {
    expect(DEFAULT_TOPUP_PRESETS_KOPECKS).toEqual([
      25_000, 50_000, 75_000, 100_000,
    ]);
  });
});
