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

// Pravki §7 v3.2: единые лейблы со страницей "Рейтинг" (Серии/Охотники/Лентяи).
const TABS: { id: LeaderboardTab; label: string; emoji: string }[] = [
  { id: "streak", label: "Серии", emoji: "🔥" },
  { id: "catches", label: "Охотники", emoji: "🎯" },
  { id: "shame", label: "Лентяи", emoji: "😴" },
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
    <article className="flex items-center gap-3 rounded-card border border-white/10 bg-surface/60 px-3 py-2.5">
      <Avatar
        src={photoSrc}
        fallback={row.first_name}
        size="sm"
        loading="eager"
        ring
      />
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
