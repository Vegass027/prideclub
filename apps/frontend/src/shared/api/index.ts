import { apiClient } from "@/shared/api/client";
import type {
  BalanceResponse,
  CatchResponse,
  LeaderboardOverviewResponse,
  LeaderboardResponse,
  MarketplaceResponse,
  MembersResponse,
  TodayResponse,
  TopupResponse,
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
};

export const usersApi = {
  me: () => apiClient.get<unknown>("/me").then((r) => r.data),
};
