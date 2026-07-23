import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLeaderboardOverview, type LeaderboardTab } from "@/shared/hooks";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
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
  const navigate = useNavigate();
  const [tab, setTab] = useState<LeaderboardTab>("streak");
  const { data, isLoading, isError, error } = useLeaderboardOverview(tab);

  return (
    <ScreenLayout>
      <PageHeader
        title="Рейтинг"
        subtitle="Топ по каждому клубу, в котором ты состоишь"
      />

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}

      {isError && (
        <EmptyState icon="⚠️" title="Не удалось загрузить" description={String(error)} />
      )}

      {!isLoading && !isError && (data?.clubs.length ?? 0) === 0 && (
        <EmptyState
          icon="🏪"
          title="Ты ещё не в клубах"
          description="Вступи в любой клуб — здесь будет рейтинг участников."
          action={
            <Button onClick={() => navigate("/marketplace")} className="mt-3 px-4 py-2 text-sm">
              Выбрать клуб
            </Button>
          }
        />
      )}

      {!isLoading && !isError && data && data.clubs.length > 0 && (
        <div className="space-y-3">
          {data.clubs.map((club) => (
            <ClubTopBlock
              key={club.habit_id}
              habitId={club.habit_id}
              title={club.title}
              membersCount={club.members_count}
              top={club.top}
              metricLabel={data.metric_label}
            />
          ))}
        </div>
      )}

      <BottomNav />
    </ScreenLayout>
  );
}

interface ClubTopBlockProps {
  habitId: string;
  title: string;
  membersCount: number;
  top: { rank: number; first_name: string; metric_value: number; membership_id: string }[];
  metricLabel: string;
}

function ClubTopBlock({ habitId, title, membersCount, top, metricLabel }: ClubTopBlockProps) {
  const navigate = useNavigate();
  return (
    <article className="rounded-card border border-white/5 bg-surface p-4 shadow-card">
      <header className="mb-3 flex items-center justify-between">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-text">〖{title}〗</h3>
          <p className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">
            {membersCount} участников
          </p>
        </div>
      </header>

      {top.length === 0 ? (
        <p className="py-3 text-center text-xs text-muted">пока никто не отметился</p>
      ) : (
        <ol className="mb-3 space-y-1">
          {top.map((row) => (
            <li
              key={row.membership_id}
              className="flex items-center gap-3 rounded-md bg-canvas/60 px-2.5 py-1.5"
            >
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
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
              </span>
              <span className="flex-1 truncate text-sm text-text">{row.first_name}</span>
              <span className="text-sm font-bold tabular-nums text-primary">
                {row.metric_value} {metricLabel}
              </span>
            </li>
          ))}
        </ol>
      )}

      <Button
        onClick={() => navigate(`/habits/${habitId}/leaderboard`)}
        variant="secondary"
        className="min-h-0 w-full px-3 py-1.5 text-xs"
      >
        Открыть клуб →
      </Button>
    </article>
  );
}
