import { formatDate } from "@/shared/utils/format";
import type { CharacterStatOut } from "@/shared/types";

interface FrozenStatBannerProps {
  stats: CharacterStatOut[];
}

/**
 * Phase 3 v2 Task 3.9: баннер о замороженных характеристиках.
 * Возвращает null если ничего не заморожено — компонент просто
 * не рендерится в DOM.
 *
 * CTA-кнопки «Продлить подписку / Пополнить депозит / Сделать чек-ин»
 * сознательно не делаем навигационными (Task 4.11 вне scope, см. план):
 * показываем текст-only hints, чтобы пользователь понимал ЧТО делать,
 * но реальная навигация — за пределами Task 3.9.
 */
export function FrozenStatBanner({ stats }: FrozenStatBannerProps) {
  const frozen = stats.filter((s) => s.is_frozen);
  if (frozen.length === 0) return null;

  const label =
    frozen.length === 1
      ? "характеристика заморожена"
      : frozen.length < 5
        ? "характеристики заморожены"
        : "характеристик заморожено";

  return (
    <section
      className="rounded-card border border-warning/30 bg-warning/10 p-4 shadow-card"
      role="alert"
    >
      <h3 className="mb-2 text-sm font-semibold text-warning">
        ❄ {frozen.length} {label}
      </h3>
      <p className="mb-3 text-xs text-muted">
        30+ дней без чек-ина. Чтобы разморозить: продли подписку,
        пополни депозит (если нужно) и сделай чек-ин.
      </p>
      <ul className="mb-3 space-y-1">
        {frozen.map((s) => (
          <li key={s.stat_definition_id} className="text-xs text-text">
            <strong>
              {s.stat_icon} {s.stat_name}
            </strong>
            {" — заморожена"}
            {s.last_checkin_at && <> с {formatDate(s.last_checkin_at)}</>}
            {s.frozen_reason_text && <>: «{s.frozen_reason_text}»</>}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2 text-xs text-muted">
        <span>→ Продлить подписку</span>
        <span aria-hidden="true">·</span>
        <span>→ Пополнить депозит</span>
        <span aria-hidden="true">·</span>
        <span>→ Сделать чек-ин</span>
      </div>
    </section>
  );
}