import { useEffect, useId, useRef, useState } from "react";
import { useStatDefinitions } from "../hooks";

export interface StatDefinitionSelectProps {
  /** Current value (UUID str). null = no stat chosen (Edit mode). */
  value: string | null;
  /** Change handler. Передаёт null если выбрана "— Не выбрано —". */
  onChange: (v: string | null) => void;
  /**
   * REQUIRED vs OPTIONAL:
   * true  = "— Не выбрано —" опция скрыта; submit disabled пока ничего не выбрано.
   * false = "— Не выбрано —" опция доступна; submit НЕ disabled при null.
   */
  required?: boolean;
  /** Disabled interaction (catalog loading / submission in flight). */
  disabled?: boolean;
  /** Inline field error message. */
  error?: string | null;
  /** Whether to mark field as touched (red border). */
  touched?: boolean;
  /** ARIA-describedby для hint. */
  hint?: string;
  /**
   * ⚠️ Edge case (Task 3.8): deactivated id от saved habit.
   * Если задан — show banner "характеристика не найдена в активном каталоге",
   * submit НЕ блокируется (legacy-клубы могут нуждаться в сохранении).
   */
  deactivatedIdWarning?: boolean;
}

/**
 * Phase 3 v2 Task 3.8 + fix: custom-styled dropdown для stat_definition_id.
 *
 * Используется в HabitCreatePage (required=true) и HabitEditForm
 * (required=false, плюс deactivatedIdWarning для edge case).
 *
 * Дизайн: нативный `<select>` заменён на custom button + popup listbox,
 * потому что нативный select рендерится ОС-стилем (Telegram iOS/Android
 * — белый/чёрный dropdown, не вписывается в dark Telegram Mini App).
 * Popup оформлен в токенах дизайна: `rounded-card`, `bg-surface`,
 * `border-white/10`, `shadow-lg` — те же что и в AdminHabitCard.
 *
 * Закрытие:
 * — click снаружи (document mousedown listener);
 * — Escape;
 * — выбор option (auto-close);
 * — toggle trigger button.
 *
 * ⚠️ Label рендерится родителем (FieldRow label="Характеристика"),
 * НЕ внутри этого компонента — раньше был дубль «Характеристика»
 * (Task 3.8 Bug Fix #1).
 *
 * Sentinel null-value: для null показываем текст «— Не выбрано —»
 * (если !required). При выборе этой опции onChange(null).
 */
export function StatDefinitionSelect({
  value,
  onChange,
  required = false,
  disabled = false,
  error = null,
  touched = false,
  hint,
  deactivatedIdWarning = false,
}: StatDefinitionSelectProps) {
  const { data, isLoading, isError, refetch } = useStatDefinitions();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const listId = useId();

  // ⚠️ Edge case: stat_definition_id от saved habit не в каталоге
  // (был деактивирован или удалён). Banner показывает warning, но submit
  // НЕ блокируется — админ может очистить выбор или выбрать другую.
  const showDeactivatedWarning = deactivatedIdWarning && !!value;

  // Lookup selected item для отображения в trigger.
  const items = data?.items ?? [];
  const selected = items.find((sd) => sd.id === value);
  const triggerLabel = selected
    ? `${selected.icon} ${selected.name}`
    : required
      ? "Выберите характеристику…"
      : "— Не выбрано —";

  // === Outside-click + Esc handlers ===
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // === Render: loading state ===
  if (isLoading) {
    return (
      <div className="flex flex-col gap-1">
        <div className="text-sm text-gray-500" role="status">
          Загружаю каталог характеристик…
        </div>
      </div>
    );
  }

  // === Render: error state ===
  if (isError) {
    return (
      <div className="flex flex-col gap-1">
        <div
          className="text-sm text-red-600 flex items-center gap-2"
          role="alert"
        >
          Не удалось загрузить каталог характеристик.
          <button
            type="button"
            onClick={() => refetch()}
            className="text-xs underline"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  // === Render: loaded state (custom dropdown) ===
  const hasError = Boolean(touched && error);
  return (
    <div ref={containerRef} className="flex flex-col gap-1 relative">
      <button
        ref={buttonRef}
        type="button"
        data-testid="stat-definition-select"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-describedby={hint ? "stat-def-hint" : undefined}
        disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}
        className={[
          "w-full min-h-[44px] rounded-card border bg-surface px-3 py-2 text-sm",
          "flex items-center justify-between gap-2 text-left",
          "transition focus:outline-none focus:ring-2 focus:ring-primary/40",
          hasError ? "border-red-500" : "border-white/10",
          disabled
            ? "opacity-60 cursor-not-allowed"
            : "hover:border-white/20",
          showDeactivatedWarning ? "border-amber-500" : "",
        ].join(" ")}
      >
        <span
          className={
            selected
              ? "text-text"
              : required
                ? "text-muted"
                : "text-muted"
          }
        >
          {triggerLabel}
        </span>
        <svg
          aria-hidden="true"
          className={`h-4 w-4 shrink-0 transition-transform ${
            open ? "rotate-180" : ""
          }`}
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M6 8l4 4 4-4"
          />
        </svg>
      </button>

      {open && (
        <ul
          id={listId}
          role="listbox"
          data-testid="stat-definition-listbox"
          aria-label="Каталог характеристик"
          className="absolute z-50 mt-1 w-full max-h-60 overflow-auto rounded-card border border-white/10 bg-surface shadow-lg py-1"
        >
          {!required && (
            <li
              role="option"
              aria-selected={value === null}
              data-testid="stat-definition-option-none"
              onClick={() => {
                onChange(null);
                setOpen(false);
                buttonRef.current?.focus();
              }}
              className={[
                "px-3 py-2 text-sm cursor-pointer flex items-center gap-2",
                value === null
                  ? "bg-primary/15 text-text"
                  : "text-muted hover:bg-canvas/60",
              ].join(" ")}
            >
              — Не выбрано —
            </li>
          )}
          {items.map((sd) => {
            const selected = sd.id === value;
            return (
              <li
                key={sd.id}
                role="option"
                aria-selected={selected}
                data-testid={`stat-definition-option-${sd.id}`}
                onClick={() => {
                  onChange(sd.id);
                  setOpen(false);
                  buttonRef.current?.focus();
                }}
                className={[
                  "px-3 py-2 text-sm cursor-pointer flex items-center gap-2",
                  selected
                    ? "bg-primary/15 text-text"
                    : "text-text hover:bg-canvas/60",
                ].join(" ")}
              >
                <span aria-hidden="true">{sd.icon}</span>
                <span>{sd.name}</span>
              </li>
            );
          })}
        </ul>
      )}

      {showDeactivatedWarning && (
        <div
          id="stat-def-hint"
          role="status"
          className="text-xs text-amber-700 mt-1"
          data-testid="deactivated-warning"
        >
          ⚠️ Сохранённая характеристика не найдена в активном каталоге.
          Возможно, она была деактивирована. Выберите другую или очистите
          выбор.
        </div>
      )}
      {hint && !showDeactivatedWarning && (
        <div id="stat-def-hint" className="text-xs text-gray-500">
          {hint}
        </div>
      )}
      {touched && error && (
        <div className="text-xs text-red-600 mt-1" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}