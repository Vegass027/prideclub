/**
 * Зеркало apps/backend/app/core/constants.py:CheckinRejectCode.
 *
 * Pravki §Z-22 (prefilter holes, 5-round fix). Значения должны точно
 * совпадать с backend. Тест в __tests__/checkinReject.test.ts проверяет
 * равенство — если в backend enum добавлен/переименован ключ, руками
 * обновляем здесь.
 *
 * Канонический порядок проверок (See backend enum docstring):
 *   1. Structural: habit → membership → paused/left → window → topic → forwarded
 *   2. State-of-day: caught_today → checkin_already_exists → joined_late
 *   3. Proof validation: wrong_type/too_short/stale_message/empty
 *
 * В TS объектном литерале порядок ключей не влияет на тип/маппинг,
 * поэтому в этом файле сгруппировано по семантике, а не по порядку.
 */
export const CheckinRejectCode = {
  // 1. Structural
  HABIT_NOT_FOUND: "habit_not_found",
  MEMBERSHIP_NOT_FOUND: "membership_not_found",
  MEMBERSHIP_NOT_ACTIVE: "membership_not_active",
  // Pravki-subscription-2026-08-17 §Z-22 (canonical #6): подписка истекла.
  // ВЫШЕ MEMBERSHIP_PAUSED — "продли подписку" лечит и подписку, и (через
  // recompute пауз) возможный PAUSED. "Пополни депозит" лечит ТОЛЬКО PAUSED,
  // а подписку не лечит → пользователь зациклится на ошибке PAUSED после topup.
  SUBSCRIPTION_EXPIRED: "subscription_expired",
  MEMBERSHIP_PAUSED: "membership_paused",
  MEMBERSHIP_LEFT: "membership_left",
  WINDOW_CLOSED: "checkin_window_closed",
  WRONG_TOPIC: "not_checkin_topic",
  FORWARDED: "forwarded",
  // 2. State-of-day
  ALREADY_CAUGHT: "caught_today",
  ALREADY_CHECKED_IN: "checkin_already_exists",
  JOINED_LATE: "joined_late",
  // 3. Proof validation
  WRONG_TYPE: "wrong_type",
  TOO_SHORT: "too_short",
  STALE_MESSAGE: "stale_message",
  EMPTY_TEXT: "empty",
} as const;

export type CheckinRejectCode =
  (typeof CheckinRejectCode)[keyof typeof CheckinRejectCode];
