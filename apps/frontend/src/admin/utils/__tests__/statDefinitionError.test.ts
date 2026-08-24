import { describe, expect, it } from "vitest";
import { formatStatDefinitionApiError } from "../statDefinitionError";

describe("formatStatDefinitionApiError", () => {
  it("maps habit_stat_definition_not_found to expected message and field", () => {
    const err = {
      response: {
        data: { code: "habit_stat_definition_not_found" },
      },
    };
    const result = formatStatDefinitionApiError(err);
    expect(result).not.toBeNull();
    expect(result?.field).toBe("stat_definition_id");
    expect(result?.message).toMatch(/не найдена/);
    expect(result?.message).toMatch(/активном каталоге/);
  });

  it("maps habit_stat_definition_inactive to expected message and field", () => {
    const err = {
      response: {
        data: { code: "habit_stat_definition_inactive" },
      },
    };
    const result = formatStatDefinitionApiError(err);
    expect(result).not.toBeNull();
    expect(result?.field).toBe("stat_definition_id");
    expect(result?.message).toMatch(/деактивирована/);
    expect(result?.message).toMatch(/другую/);
  });

  it("returns null for unrelated error codes (network / generic 500 / missing)", () => {
    expect(formatStatDefinitionApiError(null)).toBeNull();
    expect(formatStatDefinitionApiError({})).toBeNull();
    expect(
      formatStatDefinitionApiError({ response: { data: {} } }),
    ).toBeNull();
    expect(
      formatStatDefinitionApiError({
        response: { data: { code: "habit_title_too_short" } },
      }),
    ).toBeNull();
    expect(
      formatStatDefinitionApiError({
        response: { data: { code: "missing_init_data" } },
      }),
    ).toBeNull();
    expect(
      formatStatDefinitionApiError({
        response: { data: { code: null } },
      }),
    ).toBeNull();
  });
});
