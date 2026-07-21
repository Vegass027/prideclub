import { useState } from "react";
import { useGlobalLeaderboard, type LeaderboardTab } from "@/shared/hooks";
import { BottomNav } from "@/shared/ui/BottomNav";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Tabs } from "@/shared/ui/Tabs";

const TABS: { id: LeaderboardTab; label: string; emoji: string }[] = [
  { id: "streak", label: "Серии", emoji: "🔥" },
  { id: "catches", label: "Ловцы", emoji: "🎯" },
  { id: "shame", label: "Позор", emoji: "💀" },
];

export function GlobalLeaderboardPage() {
  const [tab, setTab] = useState<LeaderboardTab>("streak");
  const { data, isLoading, isError, error } = useGlobalLeaderboard(tab);

  const metricLabel = (t: LeaderboardTab): string =>
    t === "streak" ? "дн." : t === "catches" ? "поимок" : "штрафов";

  return (
    <ScreenLayout>
      <PageHeader title="Лидеры" subtitle="Суммарно по всем твоим клубам" />

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {isLoading && <Skeleton className="h-14 w-full" rows={5} />}

      {isError && (
        <EmptyState icon="⚠️" title="Не удалось загрузить" description={String(error)} />
      )}

      {!isLoading && !isError && (data?.items.length ?? 0) === 0 && (
        <EmptyState
          icon="📊"
          title="Пока никого нет"
          description="Будь первым — вступи в клуб и начни серию."
        />
      )}

      {!isLoading && !isError && (data?.items.length ?? 0) > 0 && (
        <ol className="space-y-1.5">
          {data!.items.map((row) => (
            <li key={row.membership_id}>
              <article className="flex items-center gap-3 rounded-card bg-surface/60 px-3 py-2.5">
                <div
                  className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold ${
                    row.rank === 1
                      ? "bg-yellow-500/20 text-yellow-300"
                      : row.rank === 2
                        ? "bg-gray-400/20 text-gray-200"
                        : row.rank === 3
                          ? "bg-orange-700/20 text-orange-300"
                          : "bg-surface text-muted"
                  }`}
                  aria-label={`Место ${row.rank}`}
                >
                  {row.rank}
                </div>
                <span className="flex-1 truncate text-sm font-medium text-text">
                  {row.first_name}
                </span>
                <span className="text-sm font-bold tabular-nums text-primary">
                  {row.metric_value} {metricLabel(tab)}
                </span>
              </article>
            </li>
          ))}
        </ol>
      )}

      <BottomNav />
    </ScreenLayout>
  );
}
