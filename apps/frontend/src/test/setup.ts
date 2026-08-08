/**
 * Setup файл для vitest — подключается через vitest.config.ts (setupFiles).
 *
 * Здесь подключаем @testing-library/jest-dom matchers (toBeInTheDocument, etc.)
 * для всех тестов. jsdom-окружение включается per-file через pragma
 * `// @vitest-environment jsdom` в начале теста.
 */
import "@testing-library/jest-dom/vitest";
