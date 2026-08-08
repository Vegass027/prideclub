import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  balanceApi,
  habitsApi,
  leaderboardApi,
  marketplaceApi,
  membersApi,
} from "@/shared/api";
import type { LeaderboardTabId } from "@/shared/types";

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
      // Pravki-deposit-sse.md §Z-4.2: после join — invalidate wallet
      // (если join шёл через успешный 200 OK без модала insufficient_deposit).
      qc.invalidateQueries({ queryKey: ["wallet"] });
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
      qc.invalidateQueries({ queryKey: ["wallet"] });
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
      qc.invalidateQueries({ queryKey: ["wallet"] });
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

export function useTopUpDeposit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ habit_id, amount_kopecks }: { habit_id: string; amount_kopecks: number }) =>
      balanceApi.topup({ habit_id, amount_kopecks }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["balance"] });
      // Pravki-deposit-sse.md §Z-4.2: после topup — invalidate wallet
      // (от этого зависит блокировка кнопки «Открыть клуб» на Today page).
      qc.invalidateQueries({ queryKey: ["wallet"] });
    },
  });
}

export type LeaderboardTab = LeaderboardTabId;

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

export function useLeaderboardOverview(tab: LeaderboardTab) {
  return useQuery({
    queryKey: ["leaderboard-overview", tab],
    queryFn: () => leaderboardApi.overview(tab),
    staleTime: 30_000,
  });
}

export function useLeaderboardClubs(tab: LeaderboardTab) {
  return useQuery({
    queryKey: ["leaderboard-clubs", tab],
    queryFn: () => leaderboardApi.clubs(tab),
    staleTime: 30_000,
  });
}

export { usePhotoBlob } from "./usePhotoBlob";
export { useTodayStream } from "./useTodayStream";
export { useWallet } from "./useWallet";
