import type { Habit } from "@/shared/types";
import { Button } from "@/shared/ui/Button";

interface HabitCardProps {
  habit: Habit;
  onJoin: (habitId: string) => void;
  onOpen: (habitId: string) => void;
  joined: boolean;
  busy: boolean;
}

const fmt = (k: number) =>
  new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 0 }).format(k / 100);

export function HabitCard({ habit, onJoin, onOpen, joined, busy }: HabitCardProps) {
  return (
    <div className="rounded-card bg-surface p-4 transition active:scale-[0.98]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold">{habit.title}</h3>
          <div className="mt-1 text-xs text-muted">
            окно {habit.checkin_window_start.slice(0, 5)}–{habit.checkin_window_end.slice(0, 5)} • {habit.members_count} участников
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold">{fmt(habit.price_month)}</div>
          <div className="text-xs text-muted">в месяц</div>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between">
        <div className="text-xs text-muted">штраф {fmt(habit.penalty_amount)} • фонд {fmt(habit.prize_pool)}</div>
        {joined ? (
          <Button variant="secondary" onClick={() => onOpen(habit.id)}>
            Открыть
          </Button>
        ) : (
          <Button variant="primary" loading={busy} onClick={() => onJoin(habit.id)}>
            Присоединиться
          </Button>
        )}
      </div>
    </div>
  );
}