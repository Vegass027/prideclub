import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  balanceApi,
  habitsApi,
  leaderboardApi,
  marketplaceApi,
  membersApi,
} from "@/shared/api";

export function useMarketplace() {
  return useQuery({
    queryKey: ["marketplace"],
    queryFn: marketplaceApi.list,
    staleTime: 60_000,
  });
}

export function useToday(habitId: string | undefined) {
  return useQuery({
    queryKey: ["today", habitId],
    queryFn: () => habitsApi.today(habitId!),
    enabled: Boolean(habitId),
    staleTime: 30_000,
  });
}

export function useJoinHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (habitId: string) => habitsApi.join(habitId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["marketplace"] });
      qc.invalidateQueries({ queryKey: ["today"] });
    },
  });
}

export function useLeaveHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (habitId: string) => habitsApi.leave(habitId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["marketplace"] });
      qc.invalidateQueries({ queryKey: ["today"] });
    },
  });
}

export function useMembers(habitId: string | undefined) {
  return useQuery({
    queryKey: ["members", habitId],
    queryFn: () => membersApi.list(habitId!),
    enabled: Boolean(habitId),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useCatch(habitId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (violatorMembershipId: string) =>
      membersApi.catch(habitId ?? "", violatorMembershipId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["members", habitId] });
      qc.invalidateQueries({ queryKey: ["balance"] });
    },
  });
}

export function useBalance() {
  return useQuery({
    queryKey: ["balance"],
    queryFn: balanceApi.get,
    staleTime: 30_000,
  });
}

export type LeaderboardTab = "streak" | "catches" | "shame";

export function useLeaderboard(habitId: string | undefined, tab: LeaderboardTab) {
  const fn =
    tab === "streak"
      ? () => leaderboardApi.streaks(habitId!)
      : tab === "catches"
        ? () => leaderboardApi.catchers(habitId!)
        : () => leaderboardApi.shame(habitId!);
  return useQuery({
    queryKey: ["leaderboard", tab, habitId],
    queryFn: fn,
    enabled: Boolean(habitId),
    staleTime: 60_000,
  });
}

export function useMyHabits() {
  return useQuery({
    queryKey: ["my-habits"],
    queryFn: habitsApi.mine,
    staleTime: 30_000,
  });
}

export function useGlobalLeaderboard(tab: LeaderboardTab) {
  return useQuery({
    queryKey: ["global-leaderboard", tab],
    queryFn: () => leaderboardApi.global(tab),
    staleTime: 60_000,
  });
}
