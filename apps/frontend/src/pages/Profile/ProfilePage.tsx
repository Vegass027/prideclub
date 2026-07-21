import { getUser } from "@/shared/telegram/tma";
import { useBalance, useMyHabits } from "@/shared/hooks";
import { formatDateTime, formatKopecks, transactionTypeLabel } from "@/shared/utils/format";
import { BottomNav } from "@/shared/ui/BottomNav";
import { HabitNav } from "@/shared/ui/HabitNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { Transaction } from "@/shared/types";

export function ProfilePage() {
  const tgUser = getUser();
  const { data: balance, isLoading: balanceLoading } = useBalance();
  const { data: myHabits } = useMyHabits();
  const firstHabitId = myHabits?.items?.[0]?.id;

  return (
    <ScreenLayout>
      <PageHeader title="Профиль" />

      {tgUser ? (
        <section className="rounded-card border border-white/5 bg-surface p-4 shadow-card">
          <div className="flex items-center gap-3">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-primary/20 text-2xl font-bold text-primary">
              {tgUser.first_name?.charAt(0).toUpperCase() ?? "?"}
            </div>
            <div className="min-w-0">
              <p className="truncate text-base font-semibold text-text">
                {tgUser.first_name} {tgUser.last_name}
              </p>
              {tgUser.username && (
                <p className="truncate text-sm text-muted">@{tgUser.username}</p>
              )}
              <p className="text-xs text-muted">ID: {tgUser.id}</p>
            </div>
          </div>
        </section>
      ) : (
        <Skeleton className="h-20 w-full" />
      )}

      <section className="mt-4 rounded-card border border-white/5 bg-surface p-4 shadow-card">
        <p className="mb-1 text-xs uppercase tracking-wide text-muted">Депозит</p>
        {balanceLoading ? (
          <Skeleton className="h-8 w-32" />
        ) : (
          <p className="text-2xl font-bold text-text">{formatKopecks(balance?.deposit_balance ?? 0)}</p>
        )}
      </section>

      {balance && balance.history.length > 0 && (
        <section className="mt-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            История транзакций
          </h2>
          <ul className="space-y-1.5">
            {balance.history.map((tx: Transaction) => (
              <li key={tx.id}>
                <article className="flex items-center gap-3 rounded-card bg-surface/60 px-3 py-2 text-sm">
                  <span className="flex-1 truncate text-muted">{transactionTypeLabel(tx.type)}</span>
                  <span className={`font-bold tabular-nums ${tx.amount >= 0 ? "text-success" : "text-danger"}`}>
                    {tx.amount >= 0 ? "+" : ""}{formatKopecks(tx.amount)}
                  </span>
                  <span className="text-[10px] text-muted">{formatDateTime(tx.created_at)}</span>
                </article>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-4 rounded-card border border-white/5 bg-surface p-4">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          О приложении
        </h2>
        <p className="text-xs leading-relaxed text-muted">
          Habit Club — закрытые клубы дисциплины с денежными штрафами.
          Подтверждай привычку каждый день в чате клуба — деньги остаются в призовом фонде.
          Пропустил — участники «ловят» тебя, штраф уходит в фонд клуба.
        </p>
        <p className="mt-3 text-[10px] text-muted">v0.1.0 · soft-launch</p>
      </section>

      {firstHabitId ? <HabitNav habitId={firstHabitId} /> : <BottomNav />}
    </ScreenLayout>
  );
}
