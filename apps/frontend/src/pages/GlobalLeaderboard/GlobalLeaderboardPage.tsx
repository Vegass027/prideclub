import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useLeaderboardClubs, type LeaderboardTab } from "@/shared/hooks";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import type { LeaderboardClub } from "@/shared/types";

/** Pravki §7 v3.2 (ребрендинг): 3 категории с локализованными лейблами. */
const SECTIONS: { id: LeaderboardTab; label: string; emoji: string }[] = [
  { id: "streak", label: "Серии", emoji: "🔥" },
  { id: "catches", label: "Охотники", emoji: "🎯" },
  { id: "shame", label: "Лентяи", emoji: "😴" },
];

export function GlobalLeaderboardPage() {
  // Все аккордеоны закрыты по умолчанию. Юзер кликает чтобы открыть.
  const [openTab, setOpenTab] = useState<LeaderboardTab | null>(null);

  return (
    <ScreenLayout>
      <PageHeader title="Рейтинг" />

      <div className="space-y-2">
        {SECTIONS.map((section) => (
          <Accordion
            key={section.id}
            id={section.id}
            label={section.label}
            emoji={section.emoji}
            isOpen={openTab === section.id}
            onToggle={() =>
              setOpenTab((prev) => (prev === section.id ? null : section.id))
            }
          />
        ))}
      </div>

      <BottomNav />
    </ScreenLayout>
  );
}

function Accordion({
  id,
  label,
  emoji,
  isOpen,
  onToggle,
}: {
  id: LeaderboardTab;
  label: string;
  emoji: string;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <section className="overflow-hidden rounded-card border border-white/10 bg-surface">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-white/5"
        aria-expanded={isOpen}
      >
        <span className="text-xl" aria-hidden="true">
          {emoji}
        </span>
        <span className="flex-1 text-base font-semibold text-text">{label}</span>
        <span
          aria-hidden="true"
          className={`text-muted transition-transform ${isOpen ? "rotate-180" : ""}`}
        >
          ▾
        </span>
      </button>
      {isOpen && <AccordionContent id={id} />}
    </section>
  );
}

function AccordionContent({ id }: { id: LeaderboardTab }) {
  const navigate = useNavigate();
  // Phase 3 v2 Task 3.9: GlobalLeaderboardPage не показывает stat таб
  // (stat — per-habit фича). На runtime id приходит только из "streak|catches|shame",
  // но компилятор не может это вывести — narrow явно.
  const { data, isLoading, isError, error } = useLeaderboardClubs(
    id as Exclude<LeaderboardTab, "stat">,
  );

  if (isLoading) {
    return (
      <div className="space-y-2 border-t border-white/5 p-3">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="border-t border-white/5 p-3">
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить"
          description={String(error)}
        />
      </div>
    );
  }

  if (!data || data.clubs.length === 0) {
    return (
      <div className="border-t border-white/5 p-3">
        <EmptyState
          icon="🏪"
          title="Нет клубов"
          description="Вступи в любой клуб — здесь будет рейтинг."
          action={
            <Button
              onClick={() => navigate("/marketplace")}
              className="mt-3 px-4 py-2 text-sm"
            >
              Выбрать клуб
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <ul className="border-t border-white/5">
      {data.clubs.map((club) => (
        <ClubListItem
          key={club.habit_id}
          club={club}
          onClick={() =>
            navigate(`/habits/${club.habit_id}/leaderboard?tab=${id}`)
          }
        />
      ))}
    </ul>
  );
}

function ClubListItem({
  club,
  onClick,
}: {
  club: LeaderboardClub;
  onClick: () => void;
}) {
  return (
    <li className="border-b border-white/5 last:border-b-0">
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-white/5"
      >
        <span className="flex-1 truncate text-sm text-text">〖{club.title}〗</span>
        <span className="text-xs uppercase tracking-wide text-muted">
          {club.members_count} УЧАСТНИКОВ
        </span>
      </button>
    </li>
  );
}
