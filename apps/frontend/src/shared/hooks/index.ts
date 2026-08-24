import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  balanceApi,
  characterApi,
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

/**
 * Pravki-subscribe-and-join.md §Z-17.5: обёртка над balanceApi.subscribe для JoinPayModal.
 *
 * После успеха инвалидируем все ключи, которые могли измениться:
 * - `marketplace`: статус членства на странице клубов (joined/не-joined).
 * - `today`: кэш текущего клуба (после navigate на /today).
 * - `wallet`: новый deposit_balance.
 * - `balance`: для /balance endpoint'а (история транзакций).
 * - `my-habits`: список клубов юзера (для pre-check subscription_until в Z-17).
 *
 * `onSuccess` получает `data: SubscribeResponse` от бэкенда — модалка использует
 * `total_charged_kopecks` и `charged_subscription` для честного alert'а после
 * оплаты (см. §Z-17 substep 2 — gap fix для LEFT+active subscription: реальная
 * списанная сумма может отличаться от показанной в UI).
 */
export function useJoinAndPay(
  onSuccess?: (data: import("@/shared/types").SubscribeResponse) => void,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      habit_id: string;
      deposit_amount_kopecks: number;
      subscription_accepted: boolean;
      idempotency_key: string;
    }) => balanceApi.subscribe(payload),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["marketplace"] });
      qc.invalidateQueries({ queryKey: ["today"] });
      qc.invalidateQueries({ queryKey: ["wallet"] });
      qc.invalidateQueries({ queryKey: ["balance"] });
      qc.invalidateQueries({ queryKey: ["my-habits"] });
      onSuccess?.(data);
    },
  });
}

export type LeaderboardTab = LeaderboardTabId;

export function useLeaderboard(
  habitId: string | undefined,
  tab: LeaderboardTab,
  options?: { enabled?: boolean },
) {
  const fn =
    tab === "streak"
      ? () => leaderboardApi.streaks(habitId!)
      : tab === "catches"
        ? () => leaderboardApi.catchers(habitId!)
        : () => leaderboardApi.shame(habitId!);
  return useQuery({
    queryKey: ["leaderboard", tab, habitId],
    queryFn: fn,
    // enabled: внешний гейт (опциональный) ∧ habitId truthy.
    // LeaderboardPage передаёт { enabled: !isStatTab } чтобы не слать
    // streak/catches/shame запрос когда активен таб stat.
    enabled: Boolean(habitId) && (options?.enabled ?? true),
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
// Pravki §Z-21 (Item 9): useHabitSse ЗАМЕНЯЕТ useTodayStream. Multiplex SSE
// (Item 7) + event handlers для catch / you_were_caught (Item 8).
// useTodayStream оставлен как deprecated alias для backward-compat
// (если кто-то импортирует напрямую, не сломается), но TodayPage уже
// переключён на useHabitSse.
export { useHabitSse } from "./useHabitSse";
export { useTodayStream } from "./useTodayStream";
export { useWallet } from "./useWallet";

// Phase 3 v2 Task 3.9: character + per-habit stat leaderboard + level-up tracker.
export { useLevelUpStatus } from "./levelUpTracker";

/**
 * Phase 3 v2 Task 3.9: глобальная карточка персонажа.
 * staleTime=30s — данные меняются только при чек-ине/поимке (invalidate через SSE).
 */
export function useCharacter() {
  return useQuery({
    queryKey: ["character", "me"],
    queryFn: characterApi.get,
    staleTime: 30_000,
  });
}

/**
 * Phase 3 v2 Task 3.9: per-habit лидерборд по характеристике.
 * Используется в LeaderboardPage на табе «📊 Характеристика».
 * staleTime=30s — данные меняются при чек-инах участников.
 *
 * options.enabled позволяет гейтить запрос по активному табу —
 * LeaderboardPage передаёт { enabled: isStatTab } чтобы не слать stat
 * запрос когда активен streak/catches/shame.
 */
export function useHabitStatLeaderboard(
  habitId: string | undefined,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["leaderboard", "stat", habitId],
    queryFn: () => leaderboardApi.stat(habitId!),
    enabled: Boolean(habitId) && (options?.enabled ?? true),
    staleTime: 30_000,
  });
}
