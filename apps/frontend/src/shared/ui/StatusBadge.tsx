import type { CheckinStatus } from "@/shared/types";

const statusConfig: Record<CheckinStatus, { label: string; classes: string; emoji: string }> = {
  done: { label: "Выполнено", classes: "bg-success/15 text-success border-success/30", emoji: "✅" },
  missed: { label: "Просрочено", classes: "bg-danger/15 text-danger border-danger/30", emoji: "❌" },
  pending: { label: "Ожидает выполнения", classes: "bg-warning/15 text-warning border-warning/30", emoji: "⏳" },
  not_started: { label: "Не начато", classes: "bg-muted/15 text-muted border-muted/30", emoji: "💤" },
  // Pravki-bug-fixes §Z-19 (joiner-late protection):
  // нейтральный тон, без warning/danger/success — пользователь только вступил.
  joined_late: { label: "Присоединился поздно", classes: "bg-muted/15 text-muted border-muted/30", emoji: "🌙" },
  // Pravki-bug-fixes §Z-21 (caught badge): юзер пойман другим участником за
  // сегодняшний пропуск. Тон — danger/red, как missed (разница в тексте ниже
  // на TodayPage). Emoji 🎯 чтобы визуально отделить от missed (❌).
  caught: { label: "Пойман", classes: "bg-danger/15 text-danger border-danger/30", emoji: "🎯" },
};

// Defensive fallback для рассинхрона кэша браузера:
// Старый JS-бандл может быть в кэше у юзера пока новый bundle не дошёл.
// Если backend вернёт status которого нет в statusConfig (новый код, деплой
// backend раньше frontend, или наоборот), показываем raw status как fallback
// вместо crash (cfg.emoji undefined).
const FALLBACK_BADGE = {
  label: "—",
  classes: "bg-muted/15 text-muted border-muted/30",
  emoji: "•",
};

interface StatusBadgeProps {
  status: CheckinStatus;
  className?: string;
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const cfg = statusConfig[status] ?? FALLBACK_BADGE;
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