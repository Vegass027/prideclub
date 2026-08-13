/**
 * Маппинг reject-кодов чек-ина → пользовательский текст (русский).
 *
 * Pravki §Z-22 (Step 5, hole #2): закрывает баг, найденный в начале серии —
 * бот/worker возвращают код (например, "checkin_window_closed"), фронт
 * показывал его сырым текстом в showAlert(). Теперь фронт ТОЖЕ имеет
 * маппинг (симметричный apps/bot/bot/handlers/checkin_texts.py), и юзер
 * видит нормальный текст.
 *
 * Single source of truth:
 * - Backend: apps/backend/app/core/constants.py:CheckinRejectCode
 * - Frontend: apps/frontend/src/shared/types/checkinReject.ts (TS mirror)
 * - Mapper: этот файл (НЕ в types/, потому что он содержит текст, а
 *   не просто code-values).
 *
 * Если в backend enum добавлен/переименован ключ — обновляем ВСЕ ТРИ
 * места. Тест в __tests__/checkinReject.test.ts проверяет mirror на
 * равенство. Отсутствие кода здесь → REJECT_UNKNOWN.
 *
 * Canonical order v2 (см. backend enum docstring) — тексты подобраны
 * специфично для copy priority:
 *   - state-of-day (caught_today, already_checked_in) → самые конкретные
 *   - membership setup (paused = topup, left = rejoin) → actionable
 *   - time/topic (window, topic, forwarded) → общая инструкция
 *   - proof validation → формальные ошибки
 */
import { CheckinRejectCode } from "@/shared/types/checkinReject";

const REJECT_OUT_OF_WINDOW = (start: string, end: string) =>
  `⏰ Окно чек-ина на сегодня закрыто. Успей в следующий раз в окно клуба (${start}–${end}).`;

const REJECT_JOINED_LATE = (start: string, end: string) =>
  `🌙 Вы только что вступили в клуб — сегодня уже поздно для чек-ина. Окно чек-ина этого клуба: ${start}–${end}. Следующий чек-ин — завтра.`;

const REJECT_ALREADY_CHECKED_IN = () =>
  `✅ Ты уже отметился сегодня. Повторно не нужно — молодец 😉`;

const REJECT_CAUGHT_TODAY = () =>
  `🎯 Сегодня тебя уже поймали за пропуск, штраф переведён в призовой фонд клуба.`;

// NB: REJECT_PENALTY_DAY_CLOSED (cron-only, без кэтчера) на фронте НЕ
// приходит как отдельный code — worker шлёт `reason: caught_today` для
// обеих ситуаций (caught и missed), потому что в SSE payload нет
// `checkin_status`. Различие "поймали" vs "не отметился" доступно
// только в bot prefilter (state.checkin_status). Поэтому на фронте
// единственный текст для code='caught_today'. Это OK — оба сценария
// дают одинаковый финансовый исход (штраф списан), только копия разная.
// Документация: docs/06-data-model.md §4.2 + Pravki §Z-21 (Item 4).

const REJECT_FORWARDED = () =>
  `📤 Пересланные видео не принимаются — нужно записать своё, живое, прямо сейчас в этом чате.`;

const REJECT_TOO_SHORT = () =>
  `🎥 Кружок слишком короткий (минимум 3 секунды). Перезапиши подлиннее.`;

const REJECT_WRONG_TOPIC = () =>
  `📍 Это не топик чек-инов клуба. Отправь кружок в топик «Чек-ины» — он привязан к этому клубу.`;

const REJECT_WRONG_TYPE = () =>
  `⚠️ Этот клуб не принимает такой тип подтверждения. Отправь подходящий формат.`;

const REJECT_MEMBERSHIP_PAUSED = () =>
  `💤 Твоё участие в клубе сейчас на паузе — чек-ины не засчитываются. Пополни депозит в мини-аппе, чтобы продолжить.`;

const REJECT_MEMBERSHIP_LEFT = () =>
  `🚪 Ты больше не участник этого клуба — чек-ин не засчитан. Чтобы вернуться, вступи в клуб заново через мини-апп.`;

const REJECT_MEMBERSHIP_NOT_FOUND = () =>
  `⚠️ Тебя нет в этом клубе. Вступи через мини-апп, чтобы отмечаться.`;

const REJECT_HABIT_NOT_FOUND = () =>
  `⚠️ Этот клуб не найден. Возможно, ссылка устарела.`;

const REJECT_STALE_MESSAGE = () =>
  `⏰ Видео устарело — запиши новое, живое, прямо сейчас.`;

const REJECT_EMPTY_TEXT = () =>
  `⚠️ Пришёл пустой текст. Напиши что-нибудь осмысленное.`;

const REJECT_UNKNOWN = () =>
  `⚠️ Не получилось принять чек-ин. Попробуй ещё раз или напиши в поддержку.`;

export interface RejectContext {
  /** Имя юзера (first_name) — не всегда доступно, опционально. */
  name?: string;
  /** "HH:MM" — начало окна чек-ина. */
  window_start?: string;
  /** "HH:MM" — конец окна чек-ина. */
  window_end?: string;
}

/**
 * Маппинг code → текст. Аналог bot._text_for_code().
 *
 * @param code — CheckinRejectCode value (из SSE payload reason/code)
 * @param ctx  — контекст для подстановки (window_start/end, name)
 * @returns пользовательский текст на русском
 */
export function checkinRejectText(
  code: string | null | undefined,
  ctx: RejectContext = {},
): string {
  const start = ctx.window_start ?? "?";
  const end = ctx.window_end ?? "?";

  switch (code) {
    case CheckinRejectCode.WINDOW_CLOSED:
      return REJECT_OUT_OF_WINDOW(start, end);
    case CheckinRejectCode.JOINED_LATE:
      return REJECT_JOINED_LATE(start, end);
    case CheckinRejectCode.ALREADY_CHECKED_IN:
      return REJECT_ALREADY_CHECKED_IN();
    case CheckinRejectCode.ALREADY_CAUGHT:
      return REJECT_CAUGHT_TODAY();
    case CheckinRejectCode.MEMBERSHIP_PAUSED:
      return REJECT_MEMBERSHIP_PAUSED();
    case CheckinRejectCode.MEMBERSHIP_LEFT:
      return REJECT_MEMBERSHIP_LEFT();
    case CheckinRejectCode.MEMBERSHIP_NOT_FOUND:
      return REJECT_MEMBERSHIP_NOT_FOUND();
    case CheckinRejectCode.HABIT_NOT_FOUND:
      return REJECT_HABIT_NOT_FOUND();
    case CheckinRejectCode.FORWARDED:
      return REJECT_FORWARDED();
    case CheckinRejectCode.TOO_SHORT:
      return REJECT_TOO_SHORT();
    case CheckinRejectCode.WRONG_TOPIC:
      return REJECT_WRONG_TOPIC();
    case CheckinRejectCode.WRONG_TYPE:
      return REJECT_WRONG_TYPE();
    case CheckinRejectCode.STALE_MESSAGE:
      return REJECT_STALE_MESSAGE();
    case CheckinRejectCode.EMPTY_TEXT:
      return REJECT_EMPTY_TEXT();
    default:
      return REJECT_UNKNOWN();
  }
}
