import { useBalance } from "@/shared/hooks";
import { formatDateTime, formatKopecks, transactionTypeLabel } from "@/shared/utils/format";
import { BottomNav } from "@/shared/ui/BottomNav";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { Transaction } from "@/shared/types";

export function BalancePage() {
  const { data, isLoading, isError, error } = useBalance();

  if (isLoading) {
    return (
      <main className="mx-auto max-w-md px-4 py-6">
        <PageHeader title="Баланс" />
        <Skeleton className="h-32 w-full" />
        <BottomNav />
      </main>
    );
  }

  if (isError || !data) {
    return (
      <main className="mx-auto max-w-md px-4 py-6">
        <PageHeader title="Баланс" />
        <EmptyState icon="⚠️" title="Не удалось загрузить" description={String(error ?? "")} />
        <BottomNav />
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <PageHeader title="Баланс" />

      <section className="rounded-card border border-white/5 bg-surface p-6 shadow-card">
        <p className="mb-1 text-xs uppercase tracking-wide text-muted">Депозит на всех клубах</p>
        <p className="text-3xl font-bold text-text">{formatKopecks(data.deposit_balance)}</p>
      </section>

      <section className="mt-5">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          История ({data.history.length})
        </h2>
        {data.history.length === 0 ? (
          <EmptyState icon="💸" title="Пока пусто" description="Здесь появятся все твои транзакции." />
        ) : (
          <ul className="space-y-1.5">
            {data.history.map((tx) => (
              <li key={tx.id}>
                <TransactionRow tx={tx} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <BottomNav />
    </main>
  );
}

function TransactionRow({ tx }: { tx: Transaction }) {
  const positive = tx.amount > 0;
  return (
    <article className="flex items-center gap-3 rounded-card bg-surface/60 px-3 py-2.5">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm">
        {positive ? "💰" : "💸"}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-text">{transactionTypeLabel(tx.type)}</p>
        <p className="text-[10px] text-muted">{formatDateTime(tx.created_at)}</p>
      </div>
      <span
        className={`text-sm font-semibold tabular-nums ${positive ? "text-success" : "text-danger"}`}
      >
        {positive ? "+" : ""}
        {formatKopecks(tx.amount)}
      </span>
    </article>
  );
}
