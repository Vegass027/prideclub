import axios, { AxiosError, type AxiosInstance } from "axios";
import { getInitData } from "@/shared/telegram/tma";

const baseURL = import.meta.env.VITE_API_URL ?? "/api/v1";

export const apiClient: AxiosInstance = axios.create({
  baseURL,
  timeout: 15_000,
});

apiClient.interceptors.request.use((config) => {
  const initData = getInitData();
  if (initData) {
    config.headers["X-Telegram-Init-Data"] = initData;
  }
  return config;
});

/**
 * ApiError — расширенная обёртка над ошибкой axios с сохранением
 * `response.data` для handlers, которым нужны поля ошибки (например,
 * InsufficientDepositError требует required_kopecks/current_kopecks
 * из тела ответа для UI).
 *
 * Раньше интерцептор отбрасывал весь `response.data`, оставляя только
 * `code` в `error.message`. Это сломало InsufficientDepositModal —
 * пришлось сохранять данные на error.
 *
 * `error.message` остаётся = `code` (для существующих handlers, которые
 * пишут "Не удалось зачислить подписку. Попробуй ещё раз." — им code не нужен).
 * Новые handlers (InsufficientDepositModal) могут читать `error.data.code`
 * и `error.data.required_kopecks` etc.
 */
export class ApiError extends Error {
  status: number;
  data: Record<string, unknown>;

  constructor(code: string, status: number, data: Record<string, unknown>) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

apiClient.interceptors.response.use(
  (res) => res,
  (err: AxiosError<Record<string, unknown>>) => {
    const responseData = err.response?.data ?? {};
    const code = (responseData.code as string | undefined) ?? "unknown_error";
    const status = err.response?.status ?? 0;
    return Promise.reject(new ApiError(code, status, responseData));
  },
);
