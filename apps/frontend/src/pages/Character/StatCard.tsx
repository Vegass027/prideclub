import { formatDate } from "@/shared/utils/format";
import type { CharacterStatOut } from "@/shared/types";

interface StatCardProps {
  stat: CharacterStatOut;
}

/**
 * Phase 3 v2 Task 3.9: одна характеристика в CharacterPage.
 *
 * Backend уже фильтрует (value < 1 AND !is_frozen) — такие stat'ы
 * сюда не приходят (CharacterConfig.MIN_STAT_VALUE_TO_SHOW = 1).
 * UI не делает собственной фильтрации.
 *
 * Состояния:
 * - is_frozen=true → ❄ бейдж + opacity-50 + текст «Заморожена»
 *   + frozen_reason_text если есть + last_checkin_at если был.
 * - иначе (active, value>0) → нормальная карточка:
 *   иконка + название + value крупно + last_checkin_at мелко.
 */
export function StatCard({ stat }: StatCardProps) {
  const frozen = stat.is_frozen;

  return (
    <article
      className={`rounded-card border border-white/5 bg-surface p-3 shadow-card transition ${
        frozen ? "opacity-60" : ""
      }`}
      data-frozen={frozen}
    >
      <div className="flex items-center gap-3">
        <span className="text-2xl" aria-hidden="true">
          {stat.stat_icon}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-text">
            {stat.stat_name}
          </h3>
          {frozen ? (
            <p className="text-xs text-muted">
              ❄ Заморожена
              {stat.frozen_reason_text && `: «${stat.frozen_reason_text}»`}
            </p>
          ) : stat.last_checkin_at ? (
            <p className="text-xs text-muted">
              Последний чек-ин: {formatDate(stat.last_checkin_at)}
            </p>
          ) : (
            <p className="text-xs text-muted">Нет чек-инов</p>
          )}
        </div>
        <span className="rounded-full bg-canvas/60 px-3 py-1 text-sm font-bold tabular-nums text-primary">
          {stat.value}
        </span>
      </div>
    </article>
  );
}