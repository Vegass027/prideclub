import { useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  useHabitStatLeaderboard,
  useLeaderboard,
  type LeaderboardTab,
} from "@/shared/hooks";
import { Avatar } from "@/shared/ui/Avatar";
import { EmptyState } from "@/shared/ui/EmptyState";
import { HabitNav } from "@/shared/ui/HabitNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { Tabs } from "@/shared/ui/Tabs";
import type { LeaderboardEntry, StatLeaderboardEntry } from "@/shared/types";

// Pravki §7 v3.2 + Phase 3 v2 Task 3.9: единые лейблы со страницей "Рейтинг"
// (Серии/Охотники/Лентяи + Характеристика).
const TABS: { id: LeaderboardTab; label: string; emoji: string }[] = [
  { id: "streak", label: "Серии", emoji: "🔥" },
  { id: "catches", label: "Охотники", emoji: "🎯" },
  { id: "shame", label: "Лентяи", emoji: "😴" },
  { id: "stat", label: "Характеристика", emoji: "📊" },
];

// Pravki §7 v3.3 + Phase 3 v2 Task 3.9: описание категории под табами.
const TAB_DESCRIPTIONS: Record<LeaderboardTab, string> = {
  streak: "Сколько дней подряд участник отмечается без пропусков.",
  catches: "Сколько раз участник поймал нарушителей в этом клубе.",
  shame: "Сколько дней подряд участник пропустил чек-ин.",
  stat: "Сколько единиц характеристики набрал каждый в этом клубе.",
};

const EMPTY_STATES: Record<LeaderboardTab, { icon: string; title: string; description: string }> = {
  streak: {
    icon: "📊",
    title: "Пока никто не отметился",
    description: "Будь первым — открой клуб и сделай чек-ин.",
  },
  catches: {
    icon: "🎯",
    title: "Пока никто никого не поймал",
    description: "Будь первым — поймай нарушителя, пока он не отметился.",
  },
  shame: {
    icon: "😴",
    title: "Пока все молодцы",
    description: "Пропусти чек-ин — и ты автоматически попадёшь сюда.",
  },
  stat: {
    icon: "📊",
    title: "Характеристика не активирована",
    description:
      "Админ клуба ещё не выбрал характеристику. Попросите его настроить в Habit Settings.",
  },
};

const VALID_TABS = new Set<LeaderboardTab>(["streak", "catches", "shame", "stat"]);

// Склонение для "поимок" (1 раз, 2 раза, 5 раз). Применяется для catches
// (label = "раз" — одинаково для 1/2/5, но 21-22 = специальное).
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
  // (или catches/streak/stat). Если параметр невалиден — default streak.
  const initialTab = (() => {
    const raw = searchParams.get("tab");
    if (raw && VALID_TABS.has(raw as LeaderboardTab)) {
      return raw as LeaderboardTab;
    }
    return "streak" as LeaderboardTab;
  })();
  const [tab, setTab] = useState<LeaderboardTab>(initialTab);

  // ⚠ Phase 3 v2 Task 3.9 review: оба хука гейтятся по активному табу
  // через options.enabled — иначе при просмотре «Серии» всё равно шлётся
  // запрос на /habits/{id}/leaderboard (stat), и наоборот.
  const isStatTab = tab === "stat";
  const streakCatchesShame = useLeaderboard(habitId, tab, {
    enabled: !isStatTab,
  });
  const statLeaderboard = useHabitStatLeaderboard(habitId, {
    enabled: isStatTab,
  });
  const { data, isLoading, isError, error } = isStatTab
    ? statLeaderboard
    : streakCatchesShame;

  const metricLabel = (t: LeaderboardTab, value: number): string => {
    if (t === "streak") return `${value} дн.`;
    if (t === "catches") return `${value} ${pluralRaz(value)}`;
    // stat: «ед.» — единицы характеристики. Консистентно с StatusBadge
    // («{total} ед.») и FrozenStatBanner («N характеристик»). Раньше
    // использовал pluralRaz («раз»), что семантически неверно для
    // числового значения, не для количества действий.
    if (t === "stat") return `${value} ед.`;
    return `${value} штрафов`;
  };

  return (
    <ScreenLayout>
      <PageHeader title="Лидеры клуба" back backTo="/leaderboards" />

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      <p className="text-center text-xs text-muted">{TAB_DESCRIPTIONS[tab]}</p>

      {isLoading && <Skeleton className="h-14 w-full" rows={5} />}

      {isError && (
        <EmptyState icon="⚠️" title="Не удалось загрузить" description={String(error)} />
      )}

      {!isLoading && !isError && (data?.items.length ?? 0) === 0 && (
        <EmptyState
          icon={EMPTY_STATES[tab].icon}
          title={EMPTY_STATES[tab].title}
          description={EMPTY_STATES[tab].description}
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
  // Phase 3 v2 Task 3.9: stat-таб возвращает StatLeaderboardEntry
  // (нет photo_url, нет breakdown). Используем union + "in" guard.
  row: LeaderboardEntry | StatLeaderboardEntry;
  metricLabel: string;
}) {
  // photo_url приходит как "/api/v1/users/{id}/photo" — backend отдаёт
  // FileResponse. Браузер сам рендерит <img> (не usePhotoBlob).
  // StatLeaderboardEntry не имеет photo_url → null.
  const photoSrc =
    "photo_url" in row && row.photo_url
      ? new URL(row.photo_url, window.location.origin).toString()
      : null;
  // ❄ для frozen stat-участников (только StatLeaderboardEntry имеет is_frozen).
  const isFrozen = "is_frozen" in row && row.is_frozen;
  return (
    <article
      className={`flex items-center gap-3 rounded-card border border-white/10 bg-surface/60 px-2 py-2 sm:gap-4 sm:px-3 sm:py-2.5 ${
        isFrozen ? "opacity-60" : ""
      }`}
    >
      <Avatar
        src={photoSrc}
        fallback={row.first_name}
        size="xs"
        loading="eager"
        ring
        className="mr-2 sm:mr-3"
      />
      <span className="min-w-0 truncate text-xs font-medium text-text sm:text-sm">
        {isFrozen ? "❄ " : ""}
        {row.first_name}
      </span>
      <span className="border-l border-white/10 pl-2 text-xs font-bold tabular-nums text-primary sm:pl-3 sm:text-sm whitespace-nowrap">
        {metricLabel}
      </span>
      {/* breakdown есть только у LeaderboardEntry (streak/catches/shame),
          не у StatLeaderboardEntry. */}
      {"breakdown" in row && row.breakdown && (
        <span className="border-l border-white/10 pl-2 text-[10px] tabular-nums text-muted sm:pl-3 sm:text-xs whitespace-nowrap">
          📅 {row.breakdown.checkin_count}
          {" · "}🔥 {row.breakdown.streak_days}
          {" · "}🎯 {row.breakdown.catches_count}
          {" · "}😴 {row.breakdown.penalties_count}
        </span>
      )}
    </article>
  );
}
