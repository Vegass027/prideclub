import type { QueryClient } from "@tanstack/react-query";
import type { TodayResponse } from "@/shared/types";

/** Backoff между reconnect-попытками, в миллисекундах. */
const BACKOFFS_MS = [1000, 2000, 5000, 10000] as const;

/** Минимальный интерфейс EventSource — соответствует тому, что нам реально нужно. */
export interface StreamEventSource {
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
  close(): void;
  onerror: ((this: StreamEventSource, ev: Event) => unknown) | null;
}

export interface StreamEventSourceCtor {
  new (url: string): StreamEventSource;
}

/** Минимальный интерфейс Telegram WebApp API для нотификаций (toast). */
export interface TelegramWebAppLike {
  showAlert?: (message: string) => void;
}

export interface TelegramLike {
  WebApp?: TelegramWebAppLike;
}

/** Pravki-bug-fixes §Z-21 (Item 9): haptic notifications. */
export interface HapticFeedbackLike {
  impactOccurred?: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred?: (type: "error" | "success" | "warning") => void;
}

/**
 * Optional side-effect injection for haptic notifications. Если None —
 * skip (только console.warn для ошибок). Production wiring в useHabitSse.ts.
 */
export type HapticFn = (
  kind: "impact" | "notification",
  value: "light" | "medium" | "heavy" | "rigid" | "soft" | "error" | "success" | "warning",
) => void;

export interface StreamControllerOptions {
  habitId: string;
  queryClient: QueryClient;
  /** Фабрика EventSource (DI: в проде `EventSource`, в тестах мок). */
  createEventSource: StreamEventSourceCtor;
  /** Запрос токена (DI: в проде `sseTokenApi.request`, в тестах мок). */
  requestToken: (habitId: string) => Promise<{ token: string; expires_at: string }>;
  /** Планировщик таймеров (DI: в проде глобальный `setTimeout`, в тестах `vi.useFakeTimers`). */
  setTimeoutFn?: typeof setTimeout;
  clearTimeoutFn?: typeof clearTimeout;
  /** Колбэк ошибок (toast/console). DI для тестируемости. */
  onError?: (message: string) => void;
  /** Хост для SSE (DI: в проде `/api/v1`, в тестах абсолютный URL). */
  streamBaseUrl?: string;
  /** Pravki §Z-21 (Item 9): haptic feedback для catch / you_were_caught. */
  onHaptic?: HapticFn;
}

export interface StreamController {
  /** Запустить стрим. Идемпотентно: повторный вызов игнорируется, пока работает текущий. */
  start(): void;
  /** Остановить стрим, отменить pending backoff-таймер, закрыть EventSource. */
  stop(): void;
  /** Состояние для тестов (observable). */
  readonly state: {
    isStarted: boolean;
    lastEventIdUser: string | null;
    lastEventIdHabit: string | null;
    attempt: number;
  };
}

/**
 * Управляет жизненным циклом SSE-подключения для одного `habitId`.
 *
 * Reconnect-модель (см. `sse+redis.md` §2.4):
 *  1. Запросить свежий токен через `requestToken(habitId)`.
 *  2. Открыть `EventSource` с `?habit_id=…&token=…&last_event_id=…`.
 *  3. На `checkin.accepted` — `queryClient.setQueryData(["today", habitId], payload)`.
 *  4. На `checkin.rejected` — вызвать `onError(payload.message)`.
 *  5. На `onerror` (network blip, токен протух, сервер закрыл) —
 *     закрыть EventSource, подождать backoff (1→2→5→10 с, cap),
 *     повторить с шага 1. Передаём `last_event_id=<сохранённый>`,
 *     чтобы продолжить чтение Redis Stream с правильной позиции.
 *
 * **Не полагаемся на нативный auto-reconnect EventSource** — он ре-шлёт
 * тот же URL с тем же протухшим токеном, и при 401 закрывается навсегда
 * без дальнейших попыток. Telegram WebView регулярно рвёт сеть
 * (backgrounding, Wi-Fi ↔ LTE) → без ручного reconnect видим
 * "иногда работает, иногда нет".
 *
 * Чистая функция (без React-зависимостей) — `useTodayStream.ts` тонкая обёртка
 * с `useEffect`. Тестируется через vitest без `@testing-library/react`.
 */
export function createStreamController(opts: StreamControllerOptions): StreamController {
  const {
    habitId,
    queryClient,
    createEventSource,
    requestToken,
    setTimeoutFn = setTimeout,
    clearTimeoutFn = clearTimeout,
    onError = defaultOnError,
    streamBaseUrl = "/api/v1",
    onHaptic,
  } = opts;

  let isStarted = false;
  let stopped = true;
  let es: StreamEventSource | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  // Item 7: два независимых cursor'а — multiplex SSE.
  let lastEventIdUser: string | null = null;
  let lastEventIdHabit: string | null = null;
  let attempt = 0;
  let inFlight = false;

  const open = async () => {
    if (stopped || inFlight) return;
    inFlight = true;
    try {
      const res = await requestToken(habitId);
      if (stopped) return;
      const params = new URLSearchParams({ habit_id: habitId, token: res.token });
      // Item 7 multiplex: передаём оба cursor'а.
      if (lastEventIdUser) params.set("last_event_id", lastEventIdUser);
      if (lastEventIdHabit) params.set("last_event_id_habit", lastEventIdHabit);
      const url = `${streamBaseUrl}/events/stream?${params.toString()}`;
      es = new createEventSource(url);
      bindListeners(es);
    } catch {
      // 403/503/сеть — бэкофф и retry
      scheduleReconnect();
    } finally {
      inFlight = false;
    }
  };

  const bindListeners = (source: StreamEventSource) => {
    source.addEventListener("checkin.accepted", (e) => {
      if (e.lastEventId) lastEventIdUser = e.lastEventId;
      attempt = 0; // success — сброс backoff
      try {
        const today = JSON.parse(e.data) as TodayResponse;
        queryClient.setQueryData(["today", habitId], today);
      } catch {
        onError("Не удалось обработать событие чек-ина");
      }
    });

    source.addEventListener("checkin.rejected", (e) => {
      if (e.lastEventId) lastEventIdUser = e.lastEventId;
      let message = "Чек-ин отклонён";
      try {
        const payload = JSON.parse(e.data) as { message?: string };
        if (payload.message) message = payload.message;
      } catch {
        // не JSON — дефолт
      }
      onError(message);
    });

    // Item 9: you_were_caught — personal для жертвы (user-stream).
    // Invalidate today (status=caught, новый бейдж) + wallet + balance.
    source.addEventListener("you_were_caught", (e) => {
      if (e.lastEventId) lastEventIdUser = e.lastEventId;
      attempt = 0;
      queryClient.invalidateQueries({ queryKey: ["today", habitId] });
      queryClient.invalidateQueries({ queryKey: ["wallet"] });
      queryClient.invalidateQueries({ queryKey: ["balance"] });
      // Pravki Q2 разведки: invalidate в обоих событиях — безопасно
      // (react-query dedup внутри одного tick).
      if (onHaptic) {
        // haptic warning — жертва "оштрафована" тон.
        onHaptic("notification", "warning");
      }
    });

    // Item 8: catch event — broadcast в habit-stream (для всех участников).
    // У жертвы can_catch=False (Item 1+2), но у других — нужен refresh members
    // чтобы убрать бейдж «Поймать».
    source.addEventListener("catch", (e) => {
      if (e.lastEventId) lastEventIdHabit = e.lastEventId;
      attempt = 0;
      queryClient.invalidateQueries({ queryKey: ["members", habitId] });
      queryClient.invalidateQueries({ queryKey: ["today", habitId] });
      queryClient.invalidateQueries({ queryKey: ["wallet"] });
      queryClient.invalidateQueries({ queryKey: ["balance"] });
      // Invalidate wallet в обоих событиях — Q2 разведки: react-query dedup
      // безопасен.
      if (onHaptic) {
        // haptic medium — кто-то поймал кого-то в клубе.
        onHaptic("impact", "medium");
      }
    });

    source.onerror = () => {
      // Native EventSource тоже вызовет onerror при штатном закрытии через close().
      // Различить штатное закрытие от реальной ошибки мы не можем, поэтому —
      // всегда закрываем и пытаемся reconnect. Если стрим уже stopped —
      // close() ничему не мешает.
      if (es) {
        es.close();
        es = null;
      }
      scheduleReconnect();
    };
  };

  const scheduleReconnect = () => {
    if (stopped) return;
    const idx = Math.min(attempt, BACKOFFS_MS.length - 1);
    const delay = BACKOFFS_MS[idx];
    attempt += 1;
    timer = setTimeoutFn(() => {
      timer = null;
      void open();
    }, delay);
  };

  const start = () => {
    if (isStarted) return;
    isStarted = true;
    stopped = false;
    void open();
  };

  const stop = () => {
    if (!isStarted) return;
    stopped = true;
    if (timer !== null) {
      clearTimeoutFn(timer);
      timer = null;
    }
    if (es) {
      es.close();
      es = null;
    }
    isStarted = false;
  };

  return {
    start,
    stop,
    get state() {
      return { isStarted, lastEventIdUser, lastEventIdHabit, attempt };
    },
  };
}

function defaultOnError(message: string): void {
  // Используем Telegram WebApp API если доступен (Telegram Mini App),
  // иначе console.warn — не изобретаем свой toast-UI без явного запроса.
  const telegram = (typeof globalThis !== "undefined"
    ? (globalThis as { Telegram?: TelegramLike }).Telegram
    : undefined);
  const showAlert = telegram?.WebApp?.showAlert;
  if (showAlert) {
    showAlert(message);
    return;
  }
  if (typeof console !== "undefined") {
    console.warn("[useHabitSse]", message);
  }
}