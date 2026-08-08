import { apiClient } from "@/shared/api/client";
import type {
  BalanceResponse,
  CatchResponse,
  LeaderboardClubsResponse,
  LeaderboardOverviewResponse,
  LeaderboardResponse,
  MarketplaceResponse,
  MembersResponse,
  SubscribeResponse,
  TodayResponse,
  TopupResponse,
  WalletResponse,
} from "@/shared/types";

export const marketplaceApi = {
  list: () => apiClient.get<MarketplaceResponse>("/marketplace").then((r) => r.data),
};

export const habitsApi = {
  today: (habitId: string) =>
    apiClient.get<TodayResponse>(`/habits/${habitId}/today`).then((r) => r.data),
  join: (habitId: string) =>
    apiClient.post<{ ok: boolean }>(`/habits/${habitId}/join`).then((r) => r.data),
  leave: (habitId: string) =>
    apiClient.post<{ ok: boolean }>(`/habits/${habitId}/leave`).then((r) => r.data),
  mine: () => apiClient.get<MarketplaceResponse>("/me/habits").then((r) => r.data),
};

export const membersApi = {
  list: (habitId: string) =>
    apiClient.get<MembersResponse>(`/habits/${habitId}/members`).then((r) => r.data),
  catch: (habitId: string, violatorMembershipId: string) =>
    apiClient
      .post<CatchResponse>(`/habits/${habitId}/catch`, {
        violator_membership_id: violatorMembershipId,
      })
      .then((r) => r.data),
};

export const balanceApi = {
  get: () => apiClient.get<BalanceResponse>("/balance").then((r) => r.data),
  topup: (payload: { habit_id: string; amount_kopecks: number }) =>
    apiClient.post<TopupResponse>("/payments/topup", payload).then((r) => r.data),
  /**
   * Pravki-subscribe-and-join.md §Z-14: единая оплата подписки+депозита.
   * Возвращает ACTIVE membership (или реактивирует существующую PAUSED/LEFT).
   * `charged_subscription: false` если была активная подписка (списали только депозит).
   */
  subscribe: (payload: {
    habit_id: string;
    deposit_amount_kopecks: number;
    subscription_accepted: boolean;
    idempotency_key: string;
  }) =>
    apiClient
      .post<SubscribeResponse>("/payments/subscribe", payload)
      .then((r) => r.data),
};

/** Pravki-deposit-sse.md §Z-4.1: глобальный кошелёк юзера. */
export const walletApi = {
  get: () => apiClient.get<WalletResponse>("/me/wallet").then((r) => r.data),
};

export const leaderboardApi = {
  streaks: (habitId: string) =>
    apiClient
      .get<LeaderboardResponse>(`/habits/${habitId}/leaderboard/streak`)
      .then((r) => r.data),
  catchers: (habitId: string) =>
    apiClient
      .get<LeaderboardResponse>(`/habits/${habitId}/leaderboard/catches`)
      .then((r) => r.data),
  shame: (habitId: string) =>
    apiClient
      .get<LeaderboardResponse>(`/habits/${habitId}/leaderboard/shame`)
      .then((r) => r.data),
  global: (tab: "streak" | "catches" | "shame") =>
    apiClient
      .get<LeaderboardResponse>(`/leaderboard/${tab}`)
      .then((r) => r.data),
  overview: (tab: "streak" | "catches" | "shame") =>
    apiClient
      .get<LeaderboardOverviewResponse>(`/leaderboard/${tab}/overview`)
      .then((r) => r.data),
  clubs: (tab: "streak" | "catches" | "shame") =>
    apiClient
      .get<LeaderboardClubsResponse>(`/leaderboard/${tab}/clubs`)
      .then((r) => r.data),
};

export const usersApi = {
  me: () => apiClient.get<unknown>("/me").then((r) => r.data),
};
