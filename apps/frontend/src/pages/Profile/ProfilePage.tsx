import { getUser } from "@/shared/telegram/tma";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import { BottomNav } from "@/shared/ui/BottomNav";
import { HabitNav } from "@/shared/ui/HabitNav";
import { useMarketplace } from "@/shared/hooks";

export function ProfilePage() {
  const tgUser = getUser();
  const { data: marketplace } = useMarketplace();
  const firstHabitId = marketplace?.items?.[0]?.id;

  return (
    <main className="mx-auto max-w-md px-4 py-6">
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

      <section className="mt-5 rounded-card border border-white/5 bg-surface p-4">
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
    </main>
  );
}
