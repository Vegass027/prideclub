import type { SubState } from "@/shared/utils/subscriptionState";

/**
 * Pravki-subscription-2026-08-17 §Frontend (commit 4):
 * Бейдж "Подписка закончится через N дней / Подписка окончена".
 *
 * Используется в ProfilePage (size="sm" рядом с бейджем "пауза") и в TodayPage
 * (size="md" как баннер с CTA "Продлить").
 *
 * Три состояния (computeSubState):
 *   - soon (1-2 дня): warning-бейдж, жёлтый.
 *   - expired: error-бейдж, красный.
 *   - ok (>= 3 дней): НЕ показывается (UI рендерит обычный "Членство до {date}").
 *
 * Стилизация — те же токены что в StatusBadge и JoinPayModal:
 *   - soon: bg-warning/15 text-warning border-warning/30
 *   - expired: bg-danger/15 text-danger border-danger/30
 */

interface SubscriptionBadgeProps {
  state: SubState;
  size?: "sm" | "md";
  className?: string;
}

const SIZE_STYLES: Record<"sm" | "md", string> = {
  // sm: маленький inline-бейдж для ProfilePage карточки клуба.
  sm: "shrink-0 rounded-full bg-warning/15 px-2 py-0.5 text-[10px] font-medium text-warning border border-warning/30",
  // md: крупный баннер для TodayPage с CTA.
  md: "rounded-card border border-warning/30 bg-warning/10 p-3 text-sm",
};

const EXPIRED_SIZE_STYLES: Record<"sm" | "md", string> = {
  sm: "shrink-0 rounded-full bg-danger/15 px-2 py-0.5 text-[10px] font-medium text-danger border border-danger/30",
  md: "rounded-card border-2 border-danger/30 bg-danger/10 p-3 text-sm",
};

const LABELS = {
  soon1: "⚠️ через 1 день",
  soon2: "⚠️ через 2 дня",
  expired: "🚫 Подписка окончена",
} as const;

export function SubscriptionBadge({
  state,
  size = "sm",
  className = "",
}: SubscriptionBadgeProps) {
  if (state.kind === "ok") {
    // ok — без бейджа (UI показывает "Членство до {date}" отдельно).
    return null;
  }

  if (state.kind === "expired") {
    const classes = EXPIRED_SIZE_STYLES[size];
    return (
      <span className={`${classes} ${className}`} role="status" aria-label="Подписка окончена">
        {LABELS.expired}
      </span>
    );
  }

  // state.kind === "soon"
  const classes = SIZE_STYLES[size];
  const label = state.daysLeft === 1 ? LABELS.soon1 : LABELS.soon2;
  return (
    <span
      className={`${classes} ${className}`}
      role="status"
      aria-label={`Подписка закончится через ${state.daysLeft} ${state.daysLeft === 1 ? "день" : "дня"}`}
    >
      {label}
    </span>
  );
}