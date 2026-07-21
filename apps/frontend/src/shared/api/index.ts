import { apiClient } from "@/shared/api/client";

export const marketplaceApi = {
  list: () => apiClient.get<{ items: import("@/shared/types").Habit[] }>("/marketplace").then((r) => r.data),
};

export const checkinsApi = {
  today: (habitId: string) =>
    apiClient.get<import("@/shared/types").TodayResponse>(`/habits/${habitId}/today`).then((r) => r.data),
};

export const membersApi = {
  list: (habitId: string) =>
    apiClient
      .get<{ items: import("@/shared/types").MemberRow[] }>(`/habits/${habitId}/members`)
      .then((r) => r.data),
  catch: (habitId: string, violatorMembershipId: string) =>
    apiClient
      .post<{ ok: boolean; code?: string }>(`/habits/${habitId}/catch`, {
        violator_membership_id: violatorMembershipId,
      })
      .then((r) => r.data),
};

export const balanceApi = {
  get: () =>
    apiClient
      .get<{ deposit_balance: number; history: import("@/shared/types").Transaction[] }>("/balance")
      .then((r) => r.data),
};

export const leaderboardApi = {
  streaks: (habitId: string) =>
    apiClient
      .get<{ items: import("@/shared/types").LeaderboardEntry[] }>(`/habits/${habitId}/leaderboard/streak`)
      .then((r) => r.data),
  catchers: (habitId: string) =>
    apiClient
      .get<{ items: import("@/shared/types").LeaderboardEntry[] }>(`/habits/${habitId}/leaderboard/catches`)
      .then((r) => r.data),
  shame: (habitId: string) =>
    apiClient
      .get<{ items: import("@/shared/types").LeaderboardEntry[] }>(`/habits/${habitId}/leaderboard/shame`)
      .then((r) => r.data),
};