/** Phase 3 v2 Task 3.8: backend error code → UI message mapping. */

export interface FormattedStatError {
  field: "stat_definition_id";
  message: string;
}

/**
 * Извлекает habit_stat_definition_* error code из axios-style error
 * и маппит на UI message + field name (для inline field error).
 *
 * Returns null если error не связан с stat_definition (network error,
 * 401, 500, и т.д. — общий toast).
 *
 * Чистая функция — никаких React, network, mocks. Легко тестируется.
 */
export function formatStatDefinitionApiError(
  error: unknown,
): FormattedStatError | null {
  // axios-style error: { response: { data: { code: string } } }.
  const e = error as { response?: { data?: { code?: string } } };
  const code = e?.response?.data?.code;

  if (code === "habit_stat_definition_not_found") {
    return {
      field: "stat_definition_id",
      message:
        "Характеристика не найдена в активном каталоге. " +
        "Возможно, она была деактивирована. Выберите другую.",
    };
  }
  if (code === "habit_stat_definition_inactive") {
    return {
      field: "stat_definition_id",
      message: "Эта характеристика деактивирована. Выберите другую.",
    };
  }
  return null;
}
