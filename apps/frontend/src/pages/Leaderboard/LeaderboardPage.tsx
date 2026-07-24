import { useState } from "react";
import { useParams } from "react-router-dom";
import { useLeaderboard, type LeaderboardTab } from "@/shared/hooks";
import { Avatar } from "@/shared/ui/Avatar";
import { EmptyState } from "@/shared/ui/EmptyState";
import { HabitNav } from "@/shared/ui/HabitNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Tabs } from "@/shared/ui/Tabs";

const TABS: { id: LeaderboardTab; label: string; emoji: string }[] = [
  { id: "streak", label: "Серии", emoji: "🔥" },
  { id: "catches", label: "Ловцы", emoji: "🎯" },
  { id: "shame", label: "Позор", emoji: "💀" },
];

export function LeaderboardPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const [tab, setTab] = useState<LeaderboardTab>("streak");
  const { data, isLoading, isError, error } = useLeaderboard(habitId, tab);

  const metricLabel = (t: LeaderboardTab): string =>
    t === "streak" ? "дн." : t === "catches" ? "поимок" : "штрафов";

  return (
    <ScreenLayout>
      <PageHeader title="Лидеры клуба" back backTo="/leaderboards" />

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {isLoading && <Skeleton className="h-14 w-full" rows={5} />}

      {isError && (
        <EmptyState icon="⚠️" title="Не удалось загрузить" description={String(error)} />
      )}

      {!isLoading && !isError && (data?.items.length ?? 0) === 0 && (
        <EmptyState
          icon="📊"
          title="Пока никто не отметился"
          description="Будь первым — открой клуб и сделай чек-ин."
        />
      )}

      {!isLoading && !isError && (data?.items.length ?? 0) > 0 && (
        <>
          {data?.total != null && (
            <p className="text-center text-xs text-muted">
              Показаны топ-100 из {data.total}
            </p>
          )}
          <ol className="space-y-1.5">
            {data!.items.map((row) => (
              <li key={row.membership_id}>
                <LeaderboardRow row={row} metricLabel={metricLabel(tab)} />
              </li>
            ))}
          </ol>
        </>
      )}

      <HabitNav habitId={habitId!} />
    </ScreenLayout>
  );
}

function LeaderboardRow({
  row,
  metricLabel,
}: {
  row: import("@/shared/types").LeaderboardEntry;
  metricLabel: string;
}) {
  // photo_url приходит как "/api/v1/users/{id}/photo" — backend отдаёт
  // 307 redirect на Telegram CDN. Браузер сам следует редиректу, поэтому
  // используем <img> напрямую (не usePhotoBlob — тот делает blob fetch
  // через axios, который не следует 307 для blob response).
  const photoSrc = row.photo_url
    ? new URL(row.photo_url, window.location.origin).toString()
    : null;
  return (
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
      <Avatar src={photoSrc} fallback={row.first_name} size="sm" />
      <span className="flex-1 truncate text-sm font-medium text-text">
        {row.first_name}
      </span>
      <div className="flex flex-col items-end leading-tight">
        <span className="text-sm font-bold tabular-nums text-primary">
          {row.metric_value} {metricLabel}
        </span>
        <span className="text-[10px] tabular-nums text-muted">
          📅 {row.breakdown.checkin_count}
          {" · "}🔥 {row.breakdown.streak_days}
          {" · "}🎯 {row.breakdown.catches_count}
          {" · "}🚔 {row.breakdown.penalties_count}
        </span>
      </div>
    </article>
  );
}
