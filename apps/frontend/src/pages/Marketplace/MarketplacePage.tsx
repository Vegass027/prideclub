import { useMarketplace, useMyHabits } from "@/shared/hooks";
import { formatKopecks } from "@/shared/utils/format";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { JoinButton } from "@/shared/ui/JoinButton";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";

export function MarketplacePage() {
  const { data, isLoading, isError, error, refetch } = useMarketplace();
  // Feature/paused-member-ux: /me/habits теперь возвращает ACTIVE + PAUSED
  // клубы с membership_status. Используем как источник истины для isJoined
  // (юзер видит "Открыть клуб" даже если депозит пуст и membership paused).
  const { data: myHabits } = useMyHabits();

  const myClubsById = new Map(
    (myHabits?.items ?? []).map((h) => [h.id, h] as const),
  );

  if (isLoading) {
    return (
      <ScreenLayout>
        <header className="mb-4">
          <h1 className="text-2xl font-bold">Клубы</h1>
          <p className="text-sm text-muted">Загрузка...</p>
        </header>
        <div className="space-y-3">
          <Skeleton className="h-28 w-full" rows={3} />
        </div>
        <BottomNav />
      </ScreenLayout>
    );
  }

  if (isError) {
    return (
      <ScreenLayout>
        <header className="mb-4">
          <h1 className="text-2xl font-bold">Клубы</h1>
        </header>
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить клубы"
          description={String(error)}
          action={<Button onClick={() => refetch()}>Повторить</Button>}
        />
        <BottomNav />
      </ScreenLayout>
    );
  }

  const items = data?.items ?? [];

  return (
    <ScreenLayout>
      <header className="mb-4">
        <h1 className="text-2xl font-bold">Клубы</h1>
        <p className="text-sm text-muted">Выбери клуб — дисциплина начинается сейчас</p>
      </header>
      {items.length === 0 ? (
        <EmptyState
          icon="🌱"
          title="Клубы скоро появятся"
          description="Администраторы готовят первые привычки. Загляни позже."
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((h) => {
            const myClub = myClubsById.get(h.id);
            return (
              <li key={h.id}>
                <HabitListItem
                  habit={h}
                  membershipStatus={myClub?.membership_status ?? null}
                />
              </li>
            );
          })}
        </ul>
      )}
      <BottomNav />
    </ScreenLayout>
  );
}

interface HabitListItemProps {
  habit: import("@/shared/types").Habit;
  /**
   * Feature/paused-member-ux: статус membership'а юзера в этом клубе.
   * "active" | "paused" → рендерим «Открыть клуб» (без join-flow).
   * null → юзер не в клубе, рендерим JoinButton.
   */
  membershipStatus: "active" | "paused" | null;
}

function HabitListItem({ habit, membershipStatus }: HabitListItemProps) {
  const isMember = membershipStatus !== null;
  return (
    <article className="overflow-hidden rounded-card border border-white/5 bg-surface shadow-card">
      {habit.photo_url ? (
        <div className="flex w-full items-center justify-center bg-canvas/60">
          <img
            src={habit.photo_url}
            alt={habit.title}
            className="block max-h-72 w-full object-contain"
            loading="lazy"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        </div>
      ) : (
        <div
          className="flex h-32 w-full items-center justify-center bg-gradient-to-br from-primary/30 to-primary/5 text-4xl"
          aria-hidden="true"
        >
          🎯
        </div>
      )}
      <div className="p-4">
        <header className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold text-text">〖{habit.title}〗</h2>
          {habit.description && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted">{habit.description}</p>
          )}
        </div>
          <span className="shrink-0 rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
            {habit.members_count} 👤
          </span>
        </header>
      <dl className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
        <Stat label="Штраф" value={formatKopecks(habit.penalty_amount)} danger />
        <Stat label="Подписка" value={`${formatKopecks(habit.price_month)}/мес`} />
        <Stat label="Окно" value={`${habit.checkin_window_start.slice(0, 5)}–${habit.checkin_window_end.slice(0, 5)}`} />
        <Stat label="Призовой фонд" value={formatKopecks(habit.prize_pool)} success />
      </dl>
      {/*
        Feature/paused-member-ux:
        - isMember (active или paused) → "Открыть клуб" без join-flow.
          Paused-юзер попадёт на Today, где увидит баннер "пополни депозит"
          и кнопку "💰 Пополнить депозит" (см. TodayPage §Z-4.3).
        - !isMember → JoinButton с обычным JoinPayModal flow.
      */}
      {isMember ? (
        <Button
          onClick={() => (window.location.href = `/habits/${habit.id}/today`)}
          variant="secondary"
          className="w-full"
        >
          Открыть клуб →
        </Button>
      ) : (
        <JoinButton habit={habit} />
      )}
      </div>
    </article>
  );
}

function Stat({ label, value, danger, success }: { label: string; value: string; danger?: boolean; success?: boolean }) {
  const color = danger ? "text-danger" : success ? "text-success" : "text-text";
  return (
    <div className="flex flex-col">
      <dt className="text-[10px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className={`text-sm font-semibold ${color}`}>{value}</dd>
    </div>
  );
}
