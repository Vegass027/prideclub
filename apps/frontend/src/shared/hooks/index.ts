import { useQuery } from "@tanstack/react-query";
import { balanceApi, checkinsApi, leaderboardApi, marketplaceApi, membersApi } from "@/shared/api";

export function useMarketplace() {
  return useQuery({
    queryKey: ["marketplace"],
    queryFn: marketplaceApi.list,
    staleTime: 60_000,
  });
}

export function useToday(habitId: string | undefined) {
  return useQuery({
    queryKey: ["checkin", "today", habitId],
    queryFn: () => checkinsApi.today(habitId!),
    enabled: Boolean(habitId),
    staleTime: 30_000,
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

export function useBalance() {
  return useQuery({
    queryKey: ["balance"],
    queryFn: balanceApi.get,
    staleTime: 30_000,
  });
}

export function useLeaderboard(habitId: string | undefined, tab: "streak" | "catches" | "shame") {
  const fn =
    tab === "streak" ? () => leaderboardApi.streaks(habitId!) : tab === "catches" ? () => leaderboardApi.catchers(habitId!) : () => leaderboardApi.shame(habitId!);
  return useQuery({
    queryKey: ["leaderboard", tab, habitId],
    queryFn: fn,
    enabled: Boolean(habitId),
    staleTime: 60_000,
  });
}