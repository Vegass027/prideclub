import { useEffect } from "react";

interface LevelUpToastProps {
  visible: boolean;
  previousName: string | null;
  newName: string;
  onDone: () => void;
}

/**
 * Phase 3 v2 Task 3.9: ephemeral toast для level-up.
 *
 * ⚠️ Haptic вызывается ТОЛЬКО в useLevelUpStatus (hook), не здесь
 * (план §3.9 — дубль не нужен). См. levelUpTracker.ts:useEffect на
 * justLeveledUp → hapticImpact("medium").
 *
 * Auto-hide через 4 секунды через onDone callback.
 * На visible=true запускаем таймер; на visible=false или unmount —
 * чистим таймер.
 *
 * Copy:
 * - previousName !== null → «🎉 Новый статус: «{newName}»»
 * - previousName === null → «✨ Добро пожаловать: «{newName}»»
 * (защита на случай прямого вызова без calibration).
 *
 * Стиль: fixed top, slide-down через CSS transform.
 */
export function LevelUpToast({
  visible,
  previousName,
  newName,
  onDone,
}: LevelUpToastProps) {
  useEffect(() => {
    if (!visible) return;
    const timer = window.setTimeout(onDone, 4000);
    return () => window.clearTimeout(timer);
  }, [visible, onDone]);

  if (!visible) return null;

  const text =
    previousName !== null
      ? `🎉 Новый статус: «${newName}»`
      : `✨ Добро пожаловать: «${newName}»`;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 top-0 z-50 mx-auto max-w-md px-4 pt-3 animate-[slideDown_300ms_ease-out]"
    >
      <div className="rounded-card border border-primary/40 bg-primary/15 px-4 py-3 text-center shadow-lg backdrop-blur">
        <p className="text-sm font-bold text-text">{text}</p>
      </div>
    </div>
  );
}