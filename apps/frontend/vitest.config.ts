import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Vitest config для apps/frontend.
 *
 * - Default environment: `node` — для существующих тестов pure-функций
 *   (streamController, topupPresets) без React rendering.
 *
 * - React component-тесты (JoinButton.test.tsx, TodayPage.test.tsx)
 *   требуют DOM. Используют jsdom через pragma-комментарий в начале файла:
 *
 *     // @vitest-environment jsdom
 *
 *   Это позволяет компонентным тестам использовать React Testing Library
 *   (`@testing-library/react` — render, screen, waitFor), а существующим
 *   pure-функциональным тестам — оставаться в node-среде без лишних
 *   расходов на DOM-инициализацию (streamController, topupPresets).
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  test: {
    environment: "node",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
