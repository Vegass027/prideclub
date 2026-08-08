/**
 * Pure-функции для TopUpModal UX-логики.
 *
 * Вынесены из компонента чтобы быть тестируемыми без React rendering
 * (vitest 2.1.8 без jsdom — компонентные тесты требуют настройки
 * @testing-library/react, что отложено до PR #4).
 *
 * Pravki-deposit-sse.md §Z-3.4 + UX-правило от пользователя 2026-08-08:
 * - Пресеты 250/500/750/1000 ₽ (не 299/599/999/1999 из старой версии).
 * - Подсвечивать наименьший пресет, который ПОКРЫВАЕТ нужную сумму (>= required).
 * - Если required > max preset — null (UI должен показать "своя сумма" input).
 */

export const DEFAULT_TOPUP_PRESETS_KOPECKS = [
  250 * 100,
  500 * 100,
  750 * 100,
  1000 * 100,
] as const;

/**
 * @param requiredKopecks - сколько нужно (penalty - deposit из InsufficientDepositError).
 * @param presets - отсортированный по возрастанию массив пресетов.
 * @returns пресет >= requiredKopecks, или null если required > max(presets).
 *
 * Пример:
 *   pickPresetToCover(320, [25000, 50000, 75000, 100000]) === 50000 (500₽)
 *   pickPresetToCover(50, [25000, 50000, ...]) === 25000
 *   pickPresetToCover(150000, [25000, 50000, 75000, 100000]) === null
 */
export function pickPresetToCover(
  requiredKopecks: number,
  presets: readonly number[],
): number | null {
  if (requiredKopecks <= 0) {
    // 0 или отрицательное — депозит уже достаточен, не предлагаем пополнение.
    return null;
  }
  for (const p of presets) {
    if (p >= requiredKopecks) {
      return p;
    }
  }
  return null;
}

/**
 * Сколько не хватает юзеру для вступления. Всегда > 0 если меньше required.
 *
 * Пример: required=500, deposit=320 → 180 (1.80₽).
 */
export function missingKopecks(requiredKopecks: number, currentKopecks: number): number {
  const diff = requiredKopecks - currentKopecks;
  return diff > 0 ? diff : 0;
}
