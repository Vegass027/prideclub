import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
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

const VALID_TABS = new Set<LeaderboardTab>(["streak", "catches", "shame"]);

// Склонение для "поимок" (1 раз, 2 раза, 5 раз). Применяется ТОЛЬКО
// для catches (label = "раз" — одинаково для 1/2/5, но 21-22 = специальное).
// "дн." / "штрафов" — без склонения, статичные.
function pluralRaz(n: number): "раз" | "раза" {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "раз";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "раза";
  return "раз";
}

export function LeaderboardPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const [searchParams] = useSearchParams();
  // Аккордеон на /leaderboards передаёт активный tab через ?tab=shame
  // (или catches/streak). Если параметр невалиден — default streak.
  const initialTab = (() => {
    const raw = searchParams.get("tab");
    if (raw && VALID_TABS.has(raw as LeaderboardTab)) {
      return raw as LeaderboardTab;
    }
    return "streak" as LeaderboardTab;
  })();
  const [tab, setTab] = useState<LeaderboardTab>(initialTab);
  const { data, isLoading, isError, error } = useLeaderboard(habitId, tab);

  const metricLabel = (t: LeaderboardTab, value: number): string => {
    if (t === "streak") return `${value} дн.`;
    if (t === "catches") return `${value} ${pluralRaz(value)}`;
    return `${value} штрафов`;
  };

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
                <LeaderboardRow row={row} metricLabel={metricLabel(tab, row.metric_value)} />
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
  // FileResponse. Браузер сам рендерит <img> (не usePhotoBlob).
  const photoSrc = row.photo_url
    ? new URL(row.photo_url, window.location.origin).toString()
    : null;
  return (
    <article className="flex items-center gap-2 rounded-card border border-white/10 bg-surface/60 px-2 py-2 sm:gap-3 sm:px-3 sm:py-2.5">
      <Avatar
        src={photoSrc}
        fallback={row.first_name}
        size="xs"
        loading="eager"
        ring
      />
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-text sm:text-sm">
        {row.first_name}
      </span>
      <span className="border-l border-white/10 pl-2 text-xs font-bold tabular-nums text-primary sm:pl-3 sm:text-sm whitespace-nowrap">
        {metricLabel}
      </span>
      <span className="border-l border-white/10 pl-2 text-[10px] tabular-nums text-muted sm:pl-3 sm:text-xs whitespace-nowrap">
        📅 {row.breakdown.checkin_count}
        {" · "}🔥 {row.breakdown.streak_days}
        {" · "}🎯 {row.breakdown.catches_count}
        {" · "}😴 {row.breakdown.penalties_count}
      </span>
    </article>
  );
}
