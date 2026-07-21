export type ProofType = "video_note" | "photo" | "text";

export type MembershipStatus = "active" | "paused" | "left";

export interface User {
  id: number;
  username: string | null;
  first_name: string;
  bonus_points: number;
}

export interface Habit {
  id: string;
  title: string;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  penalty_amount: number;
  price_month: number;
  proof_type: ProofType;
  prize_pool: number;
  chat_id: number;
  members_count: number;
}

export interface Membership {
  id: string;
  user_id: number;
  habit_id: string;
  status: MembershipStatus;
  deposit_balance: number;
  subscription_until: string;
  auto_renew_enabled: boolean;
  joined_at: string;
}

export interface CheckinStatus {
  status: "done" | "missed" | "pending" | "not_started";
  streak_days: number;
  deadline_at: string | null;
}

export interface MemberRow {
  membership_id: string;
  user_id: number;
  first_name: string;
  username: string | null;
  status: "done" | "missed" | "pending" | "not_started";
  streak_days: number;
  can_catch: boolean;
}

export type PenaltyReason = "caught" | "window_closed_no_catch";

export interface Transaction {
  id: string;
  type: string;
  amount: number;
  balance_after: number | null;
  created_at: string;
}

export interface LeaderboardEntry {
  rank: number;
  membership_id: string;
  first_name: string;
  metric_value: number;
}

export interface TodayResponse {
  membership_id: string;
  habit_id: string;
  habit_title: string;
  status: "done" | "missed" | "pending" | "not_started";
  streak_days: number;
  deadline_at: string | null;
  proof_type: ProofType;
  deposit_balance: number;
  hint: string | null;
}

export interface BalanceResponse {
  deposit_balance: number;
  history: Transaction[];
}