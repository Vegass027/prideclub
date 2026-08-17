/**
 * Date utilities для работы с клубной таймзоной (club_tz).
 *
 * Pravki-subscription-2026-08-17 §Z-22: подписка проверяется против club_date
 * (TZ клуба через Habit.club_date), без grace period.
 *
 * Используем vanilla Intl.DateTimeFormat (нет зависимости от date-fns/dayjs),
 * работает в Telegram WebView (любой evergreen браузер).
 */

/**
 * Сегодня в указанной IANA timezone, в формате "YYYY-MM-DD".
 *
 * Лексикографическое сравнение строк "YYYY-MM-DD" = хронологическое
 * сравнение тех же дат — никаких Date-объектов, никаких TZ-багов.
 *
 * Пример: clubTodayInTz("Europe/Moscow") в 22:00 UTC = 2026-08-18 (полночь в Москве
 * уже прошла). В 18:00 UTC = 2026-08-17 (полночь в Москве ещё не наступила).
 *
 * Q2 (без grace period): сравнение через < для "истёкшей" и == для
 * "последнего валидного дня".
 */
export function clubTodayInTz(timezone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/**
 * Календарная разница между двумя ISO-датами "YYYY-MM-DD" в днях.
 *
 * Обе строки сравниваются как UTC midnight — без DST-bug'ов и без
 * конвертации в локальную TZ сервера. Math.round на случай если ms
 * содержит несколько часов из-за TZ (защита, в норме деление даёт целое).
 *
 * fromIso < toIso → положительное число (toIso "в будущем" относительно fromIso).
 * fromIso > toIso → отрицательное число (toIso "в прошлом").
 * fromIso == toIso → 0.
 */
export function calendarDaysBetween(fromIso: string, toIso: string): number {
  const ms =
    Date.parse(`${toIso}T00:00:00Z`) - Date.parse(`${fromIso}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}