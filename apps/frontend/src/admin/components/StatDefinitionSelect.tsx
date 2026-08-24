import { useStatDefinitions } from "../hooks";

export interface StatDefinitionSelectProps {
  /** Current value (UUID str). null = no stat chosen (Edit mode). */
  value: string | null;
  /** Change handler. Передаёт null если выбрана "— Не выбрано —". */
  onChange: (v: string | null) => void;
  /** REQUIRED vs OPTIONAL:
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
  /** Field label. */
  label?: string;
  /** ARIA-describedby для hint. */
  hint?: string;
  /** ⚠️ Edge case (Task 3.8): deactivated id от saved habit.
   * Если задан — show banner "характеристика не найдена в активном каталоге",
   * submit НЕ блокируется (legacy-клубы могут нуждаться в сохранении).
   */
  deactivatedIdWarning?: boolean;
}

/**
 * Phase 3 v2 Task 3.8: reusable dropdown для stat_definition_id.
 *
 * Используется в HabitCreatePage (required=true) и HabitEditForm
 * (required=false, плюс deactivatedIdWarning для edge case).
 *
 * Состояния:
 * - loading → spinner "Загружаю каталог…"
 * - error → banner "Не удалось загрузить" + Retry
 * - loaded → <select> с options по sort_order ASC.
 *
 * Sentinel "" в HTML <select> для null-value (create-mode передаёт
 * пустую строку → "" sentinel → null в payload).
 */
export function StatDefinitionSelect({
  value,
  onChange,
  required = false,
  disabled = false,
  error = null,
  touched = false,
  label = "Характеристика",
  hint,
  deactivatedIdWarning = false,
}: StatDefinitionSelectProps) {
  const { data, isLoading, isError, refetch } = useStatDefinitions();

  // ⚠️ Edge case: stat_definition_id от saved habit не в каталоге
  // (был деактивирован или удалён). Banner показывает warning, но submit
  // НЕ блокируется — админ может очистить выбор или выбрать другую.
  const showDeactivatedWarning = deactivatedIdWarning && !!value;

  // === Render: loading state ===
  if (isLoading) {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium">{label}</label>
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
        <label className="text-sm font-medium">{label}</label>
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

  // === Render: loaded state (with optional warning for deactivated id) ===
  const items = data?.items ?? [];
  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium" htmlFor="stat-definition-select">
        {label}
        {required && <span className="text-red-600"> *</span>}
      </label>
      <select
        id="stat-definition-select"
        data-testid="stat-definition-select"
        className={[
          "block w-full min-h-[44px] rounded-card border bg-surface px-3 py-2 text-sm",
          touched && error ? "border-red-500" : "border-white/10",
        ].join(" ")}
        aria-invalid={Boolean(touched && error)}
        aria-describedby={hint ? "stat-def-hint" : undefined}
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" ? null : v);
        }}
      >
        {!required && (
          <option value="">— Не выбрано —</option>
        )}
        {items.map((sd) => (
          <option key={sd.id} value={sd.id}>
            {sd.icon} {sd.name}
          </option>
        ))}
      </select>
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
