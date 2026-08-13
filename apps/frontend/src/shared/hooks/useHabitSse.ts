import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { sseTokenApi } from "@/shared/api/sseToken";
import { createStreamController, type StreamController } from "./streamController";

/** Pravki §Z-21 (Item 9): минимальный интерфейс Telegram WebApp HapticFeedback.
 * Зеркалит структуру из @/shared/telegram/tma.ts Window interface —
 * отдельный type здесь чтобы не тащить global declaration в этот hot path.
 */
interface TelegramHapticFeedback {
  impactOccurred?: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred?: (type: "error" | "success" | "warning") => void;
}

/**
 * Подписка на real-time стрим чек-инов + catch-уведомления для клуба.
 *
 * Pravki-bug-fixes §Z-21 (Item 9): ЗАМЕНЯЕТ `useTodayStream`. Открывает
 * multiplex SSE-соединение (Item 7) — один EventSource читает оба
 * стрима (user + habit) и обрабатывает:
 *  - `checkin.accepted` / `checkin.rejected` — user-stream, personal
 *    (юзер видит свой чек-ин).
 *  - `catch` — habit-stream broadcast (Item 8). У жертвы пропадает бейдж
 *    «Поймать» в /members, у других — refresh UI. Haptic medium.
 *  - `you_were_caught` — user-stream personal для жертвы (Item 8).
 *    Status меняется на «Пойман». Haptic warning.
 *
 * Тестовый lifecycle идентичен `useTodayStream` (React useEffect + cleanup).
 * Stream lifecycle инкапсулирован в чистой функции `createStreamController`
 * (multiplex Item 7, два cursor'а lastEventIdUser/lastEventIdHabit).
 *
 * Backward-compat (deploy Item 9): пользователи со старым фронтом
 * (Item 3 bundle без `?last_event_id_habit=...`) продолжат работать —
 * backend legacy fallback (Item 7 Variant 1): habit-stream НЕ подписан,
 * только user-stream. Warning-лог `sse_multiplex_drift_detected` в
 * backend помогает диагностике drift'а. Деплой frontend через nginx
 * reload (не recreate) — уже открытые EventSource соединения не рвутся.
 */
export function useHabitSse(habitId: string | undefined): void {
  const queryClient = useQueryClient();
  const controllerRef = useRef<StreamController | null>(null);

  useEffect(() => {
    if (!habitId) return undefined;

    const controller = createStreamController({
      habitId,
      queryClient,
      // `EventSource` имеет больше свойств (readyState/url/onopen/...), чем
      // наш минимальный `StreamEventSource`-интерфейс — TypeScript не делает
      // covariant constructor return structural match. Это безопасный каст
      // в одной DI-точке: контрактно `EventSource` реализует все три метода
      // (`addEventListener`, `close`, `onerror`), которые использует контроллер.
      createEventSource: EventSource as unknown as Parameters<
        typeof createStreamController
      >[0]["createEventSource"],
      requestToken: sseTokenApi.request,
      // Pravki §Z-21 (Item 9): haptic feedback через Telegram WebApp API.
      // DI — для тестируемости и graceful fallback (если WebApp не
      // инициализирован). Production вызов идёт напрямую в Telegram SDK
      // (window.Telegram.WebApp.HapticFeedback), mock в тестах.
      onHaptic: defaultOnHaptic,
    });
    controllerRef.current = controller;
    controller.start();

    return () => {
      controller.stop();
      controllerRef.current = null;
    };
  }, [habitId, queryClient]);
}

/**
 * Pravki §Z-21 (Item 9): production haptic dispatcher через Telegram
 * WebApp API. Если SDK недоступен (e.g. local dev вне Mini App) — silent noop,
 * без throw (haptic — best-effort UX enhancement, не critical path).
 */
function defaultOnHaptic(
  kind: "impact" | "notification",
  value: "light" | "medium" | "heavy" | "rigid" | "soft" | "error" | "success" | "warning",
): void {
  if (typeof window === "undefined") return;
  const telegram = (
    window as unknown as {
      Telegram?: { WebApp?: { HapticFeedback?: TelegramHapticFeedback } };
    }
  ).Telegram;
  const h = telegram?.WebApp?.HapticFeedback;
  if (!h) return;
  try {
    if (kind === "impact") {
      h.impactOccurred?.(value as Parameters<NonNullable<TelegramHapticFeedback["impactOccurred"]>>[0]);
    } else {
      h.notificationOccurred?.(value as Parameters<NonNullable<TelegramHapticFeedback["notificationOccurred"]>>[0]);
    }
  } catch {
    // silent — haptic не должен ломать UX-flow если SDK бросил.
  }
}
