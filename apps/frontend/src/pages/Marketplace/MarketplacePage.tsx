import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useJoinHabit, useMarketplace, useMyHabits } from "@/shared/hooks";
import { formatKopecks } from "@/shared/utils/format";
import type { Habit } from "@/shared/types";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PaymentModal } from "@/shared/ui/PaymentModal";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { hapticImpact, hapticNotify } from "@/shared/telegram/tma";

export function MarketplacePage() {
  const { data, isLoading, isError, error, refetch } = useMarketplace();
  const { data: myHabits } = useMyHabits();
  const navigate = useNavigate();
  const joinMutation = useJoinHabit();
  const [payingHabit, setPayingHabit] = useState<Habit | null>(null);

  const joinedIds = new Set((myHabits?.items ?? []).map((h) => h.id));

  const handleJoin = (habit: Habit) => {
    hapticImpact("medium");
    setPayingHabit(habit);
  };

  const handlePaymentSuccess = (habit: Habit) => {
    joinMutation.mutate(habit.id, {
      onSuccess: () => {
        hapticNotify("success");
        navigate(`/habits/${habit.id}/today`);
      },
      onError: () => {
        hapticNotify("error");
        alert("Не удалось зачислить подписку. Попробуй ещё раз.");
      },
    });
  };

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
            const isJoined = joinedIds.has(h.id);
            return (
              <li key={h.id}>
                <HabitListItem
                  habit={h}
                  isJoined={isJoined}
                  busy={joinMutation.isPending && joinMutation.variables === h.id}
                  onJoin={() => handleJoin(h)}
                />
              </li>
            );
          })}
        </ul>
      )}
      <PaymentModal
        habit={payingHabit}
        onClose={() => setPayingHabit(null)}
        onSuccess={handlePaymentSuccess}
      />
      <BottomNav />
    </ScreenLayout>
  );
}

interface HabitListItemProps {
  habit: Habit;
  isJoined: boolean;
  busy: boolean;
  onJoin: () => void;
}

function HabitListItem({ habit, isJoined, busy, onJoin }: HabitListItemProps) {
  return (
    <article className="overflow-hidden rounded-card border border-white/5 bg-surface shadow-card">
      {habit.photo_url ? (
        <div className="flex h-40 w-full items-center justify-center bg-canvas/60">
          <img
            src={habit.photo_url}
            alt={habit.title}
            className="block max-h-40 w-full object-contain"
            loading="lazy"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        </div>
      ) : (
        <div
          className="flex h-40 w-full items-center justify-center bg-gradient-to-br from-primary/30 to-primary/5 text-4xl"
          aria-hidden="true"
        >
          🎯
        </div>
      )}
      <div className="p-4">
        <header className="mb-2 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-text">{habit.title}</h2>
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
      {isJoined ? (
        <Button
          onClick={() => (window.location.href = `/habits/${habit.id}/today`)}
          variant="secondary"
          className="w-full"
        >
          Открыть клуб →
        </Button>
      ) : (
        <Button onClick={onJoin} loading={busy} className="w-full" variant="primary">
          Вступить
        </Button>
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
