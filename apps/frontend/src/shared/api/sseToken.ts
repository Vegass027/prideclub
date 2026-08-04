import { apiClient } from "./client";

/** Response from POST /api/v1/events/stream/token (Step 1, `c836542`). */
export interface SseTokenResponse {
  token: string;
  /** ISO 8601 UTC. */
  expires_at: string;
}

/**
 * Выдача короткоживущего JWT-токена (TTL 60 с) для открытия SSE-стрима.
 * initData уходит через interceptor `apiClient` — никаких ручных заголовков.
 *
 * Эндпоинт под `/api/v1` (initData-auth + membership-check), поэтому
 * `EventSource` с относительным URL `/api/v1/events/stream?token=…`
 * берёт токен отсюда и ходит через nginx exact-match блок (Step 5).
 */
export const sseTokenApi = {
  request: (habitId: string) =>
    apiClient
      .post<SseTokenResponse>("/events/stream/token", { habit_id: habitId })
      .then((r) => r.data),
};