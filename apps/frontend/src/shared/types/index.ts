export type ProofType = "video_note" | "photo" | "text";

export type MembershipStatus = "active" | "paused" | "left";

// Pravki-bug-fixes §Z-19 (joiner-late protection): пользователь вступил
// в клуб сегодня ПОСЛЕ checkin_window_end. can_catch=False в /members
// (status != 'missed'). На TodayPage самого юзера показывается мягкий
// текст "Вы вступили после чек-ина, следующая отметка — завтра".
//
// Pravki-bug-fixes §Z-21 (caught badge): "caught" добавится в §Z-21.3.
export type CheckinStatus =
  | "done"
  | "missed"
  | "pending"
  | "not_started"
  | "joined_late"
  // Pravki-bug-fixes §Z-21 (caught badge): юзер пойман за пропуск сегодня.
  // PenaltyService.apply_catch пишет Checkin(status='caught') (PR Item 2).
  | "caught";

export interface User {
  id: number;
  username: string | null;
  first_name: string;
}

export interface Habit {
  id: string;
  title: string;
  description: string | null;
  chat_id: number;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  penalty_amount: number;
  price_month: number;
  proof_type: ProofType;
  proof_types: ProofType[];
  prize_pool: number;
  members_count: number;
  is_active: boolean;
  photo_url: string | null;
  telegram_invite_link: string | null;
  checkin_topic_thread_id: number | null;
  chat_topic_thread_id: number | null;
}

/**
 * Feature/paused-member-ux: Habit + membership context для /api/v1/me/habits.
 *
 * Возвращает ACTIVE + PAUSED клубы юзера (LEFT исключён на бэкенде).
 * `membership_status` нужен для UI (бейдж "⏸ пауза" в Profile).
 * `subscription_until` — для badge "Членство до {date}".
 *
 * joined_at НЕ возвращается бэкендом (избыточно для карточки).
 */
export interface MyHabit extends Habit {
  membership_status: "active" | "paused";
  subscription_until: string | null;  // ISO date "YYYY-MM-DD"
}

export interface MyHabitsList {
  items: MyHabit[];
}

export interface Membership {
  id: string;
  user_id: number;
  habit_id: string;
  status: MembershipStatus;
  deposit_balance: number;
  subscription_until: string | null;
  auto_renew_enabled: boolean;
  joined_at: string;
}

export interface CheckinStatusOut {
  status: CheckinStatus;
  checkin_count: number;
  streak_days: number;
  penalties_count: number;
  penalties_total: number;
  /**
   * Pravki-paused-window-open-2026-08-14: сумма штрафа за сегодня (в копейках).
   * 0 если штрафа сегодня не было. Используется в TodayPage для условного
   * рендера: при status="missed" текст «Штраф списан в фонд» показывается
* ТОЛЬКО если penalty_for_today_kopecks > 0.
    *
    * **⚠️ С 2026-08-18 (Pravki-manual-catch-2026-08-18 §Шаг 3):**
    * `apply_window_expired` deprecated (safe no-op). В новом флоу
    * `penalty_for_today_kopecks > 0` означает только ручную поимку
    * (`apply_catch`), не авто-списание от cron. Логика отображения
    * сохранена: penalty_for_today_kopecks = 0 → UI НЕ показывает «штраф списан»
    * (а показывает «пропуск без штрафа»).
    */
   penalty_for_today_kopecks: number;
  deadline_at: string | null;
}

export interface TodayResponse {
  habit: Habit;
  membership: Membership;
  checkin: CheckinStatusOut;
}

export interface MemberRow {
  membership_id: string;
  user_id: number;
  first_name: string;
  username: string | null;
  status: CheckinStatus;
  checkin_count: number;
  can_catch: boolean;
  /**
   * Pravki-paused-frontend-2026-08-14: реальный статус membership жертвы.
   * Отличается от `status` (CheckinStatus) тем, что описывает состояние
   * членства, а не сегодняшнего чек-ина. Frontend фильтрует violators
   * только по `status === "missed" && can_catch && membership_status === "active"`
   * чтобы не предлагать ловить paused/left. Защита от race-condition с
   * переключением status остаётся на backend (MembershipNotActiveError
   * в apply_catch + re-check после user-lock).
   */
  membership_status: MembershipStatus;
  /** Relative path /api/v1/users/{id}/photo (Pravki §7.1 v3.1). NULL = no avatar. */
  photo_url: string | null;
}

export interface MembersResponse {
  items: MemberRow[];
}

export type CatchCode =
  | "ok"
  | "catcher_is_violator"
  | "violator_has_checkin"
  | "penalty_already_processed"
  | "deposit_exhausted"
  | "membership_not_active"
  | "habit_not_found"
  | "rate_limited";

export interface CatchResponse {
  ok: boolean;
  code?: CatchCode;
  amount?: number;
}

export interface Transaction {
  id: string;
  type: string;
  amount: number;
  balance_after: number | null;
  created_at: string;
}

export interface BalanceResponse {
  deposit_balance: number;
  history: Transaction[];
}

/**
 * Клуб в /me/wallet (Pravki-deposit-sse.md §Z-4.1 + Pravki-subscribe-and-join.md §Z-17 substep 1).
 *
 * `subscription_until` добавлен для pre-check на фронте: JoinButton сравнивает
 * с today и выбирает режим модалки оплаты («full» с чекбоксом подписки или
 * «deposit-only» без чекбокса, см. §Z-13.1 матрица). None если юзер ещё
 * ни разу не платил подписку (или legacy /join не устанавливал поле).
 */
export interface WalletClub {
  habit_id: string;
  title: string;
  penalty_amount: number;
  /** user.deposit_balance >= penalty_amount (результат MembershipService.recompute_pause_status). */
  can_checkin: boolean;
  /** "active" | "paused" — последний результат recompute. */
  status: MembershipStatus;
  /**
   * ISO date "YYYY-MM-DD" или null. Optional в TS потому что бэкенд
   * (Pydantic) тоже делает default None, и старые фикстуры в тестах
   * могут не передавать это поле (не влияет на тестируемое поведение).
   * Фронт-код должен явно обрабатывать null как «нет активной подписки».
   */
  subscription_until?: string | null;
}

/** Ответ GET /me/wallet (Pravki-deposit-sse.md §Z-4.1). */
export interface WalletResponse {
  /** Копейки на users.deposit_balance. */
  deposit_balance: number;
  active_clubs: WalletClub[];
}

export interface TopupResponse {
  ok: boolean;
  transaction_id?: string;
  new_deposit_balance?: number;
  code?: string;
}

/**
 * Pravki-subscribe-and-join.md §Z-12.1: ответ POST /api/v1/payments/subscribe.
 *
 * `charged_subscription: true` — списали price_month + deposit (новое
 * вступление или истёкшая подписка). `false` — списали только deposit
 * (была активная подписка, не трогаем).
 */
export interface SubscribeResponse {
  ok: boolean;
  transaction_id: string;
  membership_id: string;
  new_deposit_balance: number;
  subscription_until: string; // ISO date "YYYY-MM-DD"
  total_charged_kopecks: number;
  charged_subscription: boolean;
}

export interface LeaderboardBreakdown {
  /** Total done чекинов за всё время (sortable в табе "Серии"). */
  checkin_count: number;
  /** Consecutive streak от today (0 если сегодня не отмечен). */
  streak_days: number;
  /** Сколько раз поймали как нарушителя (sortable в табе "Позор"). */
  penalties_count: number;
  /** Сколько раз сам поймал других (sortable в табе "Ловцы"). */
  catches_count: number;
}

export interface LeaderboardEntry {
  rank: number;
  membership_id: string;
  first_name: string;
  metric_value: number;
  breakdown: LeaderboardBreakdown;
  // Относительный путь /api/v1/users/{id}/photo → backend делает 307 redirect
  // на Telegram CDN. null = нет аватарки или cron не подтянул → инициалы.
  photo_url: string | null;
}

export interface LeaderboardResponse {
  items: LeaderboardEntry[];
  // Общее число юзеров с ненулевой метрикой до обрезки LEADERBOARD_LIMIT=100.
  // None если обрезки не было (клуб < 100 или глобально < 100).
  // UI: "Показаны топ-100 из N".
  total: number | null;
}

export type LeaderboardTabId = "streak" | "catches" | "shame";

export interface OverviewClub {
  habit_id: string;
  title: string;
  members_count: number;
  top: LeaderboardEntry[];
}

/** Клуб в списке для глобального рейтинга (Pravki §7 v3.2). */
export interface LeaderboardClub {
  habit_id: string;
  title: string;
  members_count: number;
}

export interface LeaderboardClubsResponse {
  tab: LeaderboardTabId;
  metric_label: string;
  clubs: LeaderboardClub[];
}

export interface LeaderboardOverviewResponse {
  tab: LeaderboardTabId;
  metric_label: string;
  clubs: OverviewClub[];
}

export interface MarketplaceResponse {
  items: Habit[];
}

export interface CheckinEnqueueResponse {
  accepted: boolean;
  code: string;
  checkin_id: string | null;
}
