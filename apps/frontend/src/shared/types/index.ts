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
  prize_pool: number;
  members_count: number;
  is_active: boolean;
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
  streak_days: number;
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
  streak_days: number;
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

export interface LeaderboardEntry {
  rank: number;
  membership_id: string;
  first_name: string;
  metric_value: number;
}

export interface LeaderboardResponse {
  items: LeaderboardEntry[];
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
