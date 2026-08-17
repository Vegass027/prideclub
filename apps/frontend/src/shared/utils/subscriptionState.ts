/**
 * Subscription state для бейджа "Подписка закончится через N дней / окончена".
 *
 * Pravki-subscription-2026-08-17 §Z-22 + §Frontend (commit 4):
 *
 * Семантика "сколько дней осталось" (daysLeft):
 * - subUntil == club_today → daysLeft = 1 (сегодня — последний валидный день).
 * - subUntil == club_today+1 → daysLeft = 2 (ещё 2 дня: сегодня + завтра).
 * - subUntil == club_today+2 → daysLeft = 3 → state "ok" (без бейджа).
 * - subUntil < club_today → state "expired" (бэкенд уже отверг чек-ин).
 *
 * Это "+1 shift" — день истечения включительно считается как 1 день.
 * Альтернативная формула (без +1) даёт: subUntil == club_today → daysLeft=0
 * → expired, НО бэкенд в этом случае ещё пускает чек-ин (`< club_date` →
 * блок, `==` → валиден). Прямое расхождение между бэкендом и фронтом.
 *
 * Бейдж показывается на 1-2 дня до истечения (warning) + на expired (error).
 * Для >= 3 дней бейдж НЕ показывается — нет смысла беспокоить юзера за неделю.
 *
 * Сравнение clubToday (TZ клуба через clubTodayInTz) с subUntil (Date | string).
 */

import { calendarDaysBetween, clubTodayInTz } from "./clubDate";

export type SubState =
  | { kind: "ok" } // daysLeft >= 3 — без бейджа
  | { kind: "soon"; daysLeft: 1 | 2 } // 1-2 дня осталось — warning-бейдж
  | { kind: "expired" }; // subUntil < club_today — error-бейдж

/**
 * Вычислить состояние подписки для бейджа.
 *
 * @param subscriptionUntil ISO-дата "YYYY-MM-DD" из API (Date | string | null).
 *                         null = никогда не платил → без бейджа.
 * @param clubTz IANA timezone клуба (например "Europe/Moscow").
 * @returns SubState | null. null = бейдж не показывать (нет subscription_until).
 */
export function computeSubState(
  subscriptionUntil: string | Date | null | undefined,
  clubTz: string,
): SubState | null {
  if (subscriptionUntil === null || subscriptionUntil === undefined) {
    return null;
  }

  // Нормализуем Date в "YYYY-MM-DD" (UTC midnight чтобы избежать TZ-дрифта).
  let subUntilIso: string;
  if (typeof subscriptionUntil === "string") {
    subUntilIso = subscriptionUntil;
  } else {
    subUntilIso = subscriptionUntil.toISOString().slice(0, 10);
  }

  const clubToday = clubTodayInTz(clubTz);
  const calendarDiff = calendarDaysBetween(clubToday, subUntilIso);

  // subUntil < club_today → истекла.
  if (calendarDiff < 0) {
    return { kind: "expired" };
  }

  // +1 shift: день истечения включительно = 1 день.
  const daysLeft = calendarDiff + 1;
  if (daysLeft >= 3) {
    return { kind: "ok" };
  }
  // 1 или 2 дня осталось.
  return { kind: "soon", daysLeft: daysLeft as 1 | 2 };
}