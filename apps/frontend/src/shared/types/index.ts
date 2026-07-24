export type ProofType = "video_note" | "photo" | "text";

export type MembershipStatus = "active" | "paused" | "left";

export type CheckinStatus = "done" | "missed" | "pending" | "not_started";

export interface User {
  id: number;
  username: string | null;
  first_name: string;
  bonus_points: number;
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

export interface TopupResponse {
  ok: boolean;
  transaction_id?: string;
  new_deposit_balance?: number;
  code?: string;
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

export interface OverviewClub {
  habit_id: string;
  title: string;
  members_count: number;
  top: LeaderboardEntry[];
}

export interface LeaderboardOverviewResponse {
  tab: "streak" | "catches" | "shame";
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
