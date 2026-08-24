import type { CharacterStatusInfo } from "@/shared/types";

interface StatusBadgeProps {
  status: CharacterStatusInfo;
  total: number;
}

/**
 * Phase 3 v2 Task 3.9: top-card персонажа.
 *
 * Логика прогресс-бара:
 * - next_threshold !== null И next_status !== null → есть прогресс.
 *   Бар показывает заполнение `(total / next_threshold) * 100%`,
 *   подпись `+{next_threshold - total} до "{next_status}"`.
 * - next_threshold === null → юзер на максимальной ступени,
 *   показываем «🏆 Максимальная ступень» без прогресс-бара.
 *
 * Progress clamp на 100% — defensive (backend гарантирует, что не будет,
 * но UI robust на случай drift'а).
 */
export function StatusBadge({ status, total }: StatusBadgeProps) {
  const hasProgress =
    status.next_threshold !== null && status.next_status !== null;
  const fillPercent = hasProgress
    ? Math.min(100, Math.round((total / status.next_threshold!) * 100))
    : 0;
  const remaining = hasProgress ? status.next_threshold! - total : 0;

  return (
    <section
      className="rounded-card border border-white/5 bg-gradient-to-br from-primary/30 to-primary/5 p-4 shadow-card"
      aria-label="Текущий статус персонажа"
    >
      <div className="flex items-center gap-3">
        <span className="text-4xl" aria-hidden="true">
          {status.icon}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs uppercase tracking-wide text-muted">
            Сейчас ты
          </p>
          <h2 className="truncate text-xl font-bold text-text">
            «{status.name}»
          </h2>
        </div>
        <span className="rounded-full bg-canvas/60 px-3 py-1 text-xs font-semibold tabular-nums text-text">
          {total} ед.
        </span>
      </div>

      {hasProgress ? (
        <div className="mt-3">
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-canvas/60"
            role="progressbar"
            aria-valuenow={fillPercent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Прогресс до «${status.next_status}»`}
          >
            <div
              className="h-full bg-primary transition-all duration-300"
              style={{ width: `${fillPercent}%` }}
            />
          </div>
          <p className="mt-2 text-xs text-muted">
            +{remaining} до «{status.next_status}»
          </p>
        </div>
      ) : (
        <p className="mt-3 text-xs font-medium text-success">
          🏆 Максимальная ступень
        </p>
      )}
    </section>
  );
}