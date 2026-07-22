import { Link } from "react-router-dom";
import type { AdminHabit } from "../api";

interface AdminHabitCardProps {
  habit: AdminHabit;
  onToggle: (habitId: string, nextActive: boolean) => void;
  onArchive: (habitId: string) => void;
  onRestore: (habitId: string) => void;
  busy: boolean;
}

const formatRub = (kopecks: number): string => {
  const rub = Math.round(kopecks / 100);
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(rub);
};

export function AdminHabitCard({
  habit,
  onToggle,
  onArchive,
  onRestore,
  busy,
}: AdminHabitCardProps) {
  const isArchived = habit.archived_at !== null;
  const stat = `${habit.stat_icon ?? "🔥"} ${habit.stat_name}`;
  const window = `${habit.checkin_window_start.slice(0, 5)}–${habit.checkin_window_end.slice(0, 5)}`;

  return (
    <article className="rounded-card border border-white/5 bg-surface p-4">
      <header className="mb-2 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold text-text">{habit.title}</h3>
          <p className="mt-0.5 truncate text-xs text-muted">
            {stat} · окно {window} · {habit.timezone}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-xs ${
            isArchived
              ? "border-danger/30 bg-danger/10 text-danger"
              : habit.is_active
                ? "border-success/30 bg-success/10 text-success"
                : "border-muted/30 bg-muted/10 text-muted"
          }`}
        >
          {isArchived ? "В архиве" : habit.is_active ? "Активен" : "Скрыт"}
        </span>
      </header>

      <dl className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-muted">Цена/мес</dt>
          <dd className="text-text">{formatRub(habit.price_month)}</dd>
        </div>
        <div>
          <dt className="text-muted">Штраф</dt>
          <dd className="text-text">{formatRub(habit.penalty_amount)}</dd>
        </div>
        <div>
          <dt className="text-muted">Участников</dt>
          <dd className="text-text">{habit.active_members_count}</dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          to={`/habits/${habit.id}/edit`}
          className="inline-flex min-h-[36px] items-center rounded-card border border-white/10 bg-surface px-3 py-1.5 text-sm font-medium text-text transition hover:border-white/20"
          aria-label={`Изменить ${habit.title}`}
        >
          Изменить
        </Link>
        {!isArchived && (
          <button
            type="button"
            onClick={() => onToggle(habit.id, !habit.is_active)}
            disabled={busy}
            className="min-h-[36px] rounded-card border border-white/10 bg-surface px-3 py-1.5 text-sm font-medium text-text transition hover:border-white/20 disabled:opacity-50"
            aria-busy={busy}
          >
            {habit.is_active ? "Скрыть" : "Активировать"}
          </button>
        )}
        {!isArchived && (
          <button
            type="button"
            onClick={() => onArchive(habit.id)}
            disabled={busy}
            className="min-h-[36px] rounded-card border border-danger/30 bg-danger/10 px-3 py-1.5 text-sm font-medium text-danger transition hover:bg-danger/20 disabled:opacity-50"
            aria-busy={busy}
          >
            В архив
          </button>
        )}
        {isArchived && (
          <button
            type="button"
            onClick={() => onRestore(habit.id)}
            disabled={busy}
            className="min-h-[36px] rounded-card border border-success/30 bg-success/10 px-3 py-1.5 text-sm font-medium text-success transition hover:bg-success/20 disabled:opacity-50"
            aria-busy={busy}
          >
            Восстановить
          </button>
        )}
      </div>
    </article>
  );
}
