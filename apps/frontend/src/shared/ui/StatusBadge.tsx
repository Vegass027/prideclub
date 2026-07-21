import type { CheckinStatus } from "@/shared/types";

const statusConfig: Record<CheckinStatus, { label: string; classes: string; emoji: string }> = {
  done: { label: "Сделано", classes: "bg-success/15 text-success border-success/30", emoji: "✅" },
  missed: { label: "Пропуск", classes: "bg-danger/15 text-danger border-danger/30", emoji: "❌" },
  pending: { label: "Ждёт", classes: "bg-warning/15 text-warning border-warning/30", emoji: "⏳" },
  not_started: { label: "Не начато", classes: "bg-muted/15 text-muted border-muted/30", emoji: "💤" },
};

interface StatusBadgeProps {
  status: CheckinStatus;
  className?: string;
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const cfg = statusConfig[status];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${cfg.classes} ${className}`}
      role="status"
      aria-label={cfg.label}
    >
      <span aria-hidden="true">{cfg.emoji}</span>
      <span>{cfg.label}</span>
    </span>
  );
}
