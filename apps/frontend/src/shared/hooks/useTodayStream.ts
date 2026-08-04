import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { sseTokenApi } from "@/shared/api/sseToken";
import {
  createStreamController,
  type StreamController,
} from "./streamController";

/**
 * Подписка на real-time стрим чек-инов для текущего `habitId`.
 *
 * Зеркалит `useToday(habitId)` по семантике `enabled: Boolean(habitId)`:
 * пока `habitId === undefined` (роутер не резолвнулся, юзер не выбрал клуб),
 * хук не открывает EventSource и не запрашивает токен. Cleanup в
 * `useEffect` закрывает EventSource и отменяет pending backoff-таймер
 * при смене `habitId` или unmount.
 *
 * Логика стрима (reconnect-loop, last_event_id, backoff, setQueryData)
 * инкапсулирована в чистой функции `createStreamController` —
 * тестируется без React-renderer. Этот файл — тонкая обёртка с
 * `useEffect`, ответственная только за lifecycle.
 *
 * Документация по reconnect-стратегии — `sse+redis.md` §2.4.
 */
export function useTodayStream(habitId: string | undefined): void {
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
    });
    controllerRef.current = controller;
    controller.start();

    return () => {
      controller.stop();
      controllerRef.current = null;
    };
  }, [habitId, queryClient]);
}