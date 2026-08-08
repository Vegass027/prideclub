import { useQuery } from "@tanstack/react-query";
import { walletApi } from "@/shared/api";
import type { WalletResponse } from "@/shared/types";

/**
 * Pravki-deposit-sse.md §Z-4.1/Z-4.2: кэш кошелька юзера.
 *
 * `staleTime: 0` — при монтировании на новой странице всегда делаем refetch.
 * Это нужно потому что:
 * - MarketplacePage → join → переход на /today (кошелёк с новым deposit).
 * - TodayPage → стейл счёт при `useJoinHabit.onSuccess` / `useTopUpDeposit.onSuccess`.
 * - MembersPage / catch → invalidate счёт "you_were_caught" у жертвы (PR #4/#5).
 *
 * PR #2 не использует SSE для обновления wallet — invalidate делается явно
 * после useTopUpDeposit / useJoinHabit onSuccess.
 *
 * `refetchOnMount: "always"` — гарантирует свежие данные при переходе с
 * /marketplace на /today после успешного join (см. риск №1 в плане PR #2).
 */
export function useWallet() {
  return useQuery<WalletResponse>({
    queryKey: ["wallet"],
    queryFn: walletApi.get,
    staleTime: 0,
    refetchOnMount: "always",
  });
}
