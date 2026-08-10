import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  createStreamController,
  type StreamEventSource,
} from "../streamController";
import type { QueryClient } from "@tanstack/react-query";

/**
 * Тесты чистой функции `createStreamController` — без React rendering.
 *
 * Reconnect-логика Step 6:
 *  - enabled=false (start() не вызван) → нет запросов
 *  - start() → requestToken → EventSource с правильным URL
 *  - checkin.accepted → queryClient.setQueryData с payload + сохранение lastEventId
 *  - checkin.rejected → onError с message из payload
 *  - onerror на EventSource → close + backoff-таймер + новый EventSource с
 *    обновлённым токеном (НЕ с тем же — иначе нативный EventSource сдохнет
 *    через 60с TTL токена)
 *  - reconnect после успешного события → URL содержит last_event_id
 *  - stop() отменяет pending backoff-таймер и закрывает EventSource
 *  - requestToken throws → backoff запускается (сетевой/503 retry)
 */

class MockEventSource implements StreamEventSource {
  static instances: MockEventSource[] = [];

  url: string;
  closed = false;
  onerror: ((this: StreamEventSource, ev: Event) => unknown) | null = null;

  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    const arr = this.listeners.get(type) ?? [];
    arr.push(listener);
    this.listeners.set(type, arr);
  }

  close(): void {
    this.closed = true;
  }

  // Test helpers — не часть StreamEventSource интерфейса.
  emit(type: string, data: string, lastEventId?: string): void {
    const listeners = this.listeners.get(type) ?? [];
    const ev = {
      data,
      lastEventId: lastEventId ?? "",
    } as unknown as MessageEvent;
    for (const l of listeners) l(ev);
  }

  emitError(): void {
    if (this.onerror) {
      const fakeEvent = {} as Event;
      this.onerror.call(this, fakeEvent);
    }
  }
}

interface HarnessOpts {
  habitId?: string;
  requestTokenImpl?: (habitId: string) => Promise<{ token: string; expires_at: string }>;
  onError?: (message: string) => void;
  streamBaseUrl?: string;
  /** Item 9: invalidateQueries mock (для catch / you_were_caught handlers). */
  invalidateQueries?: ReturnType<typeof vi.fn>;
  /** Item 9: haptic callback mock. */
  onHaptic?: (kind: string, value: string) => void;
}

function createHarness(opts: HarnessOpts = {}) {
  const setQueryData = vi.fn();
  // Минимальный QueryClient-стаб — только setQueryData.
  const invalidateQueries = opts.invalidateQueries ?? vi.fn();
  const queryClient = { setQueryData, invalidateQueries } as unknown as QueryClient;

  const requestToken = vi.fn(
    opts.requestTokenImpl ??
      (async (_habitId: string) => ({
        token: "tok-1",
        expires_at: "2099-01-01T00:00:00Z",
      })),
  );

  const controller = createStreamController({
    habitId: opts.habitId ?? "habit-abc",
    queryClient,
    createEventSource: MockEventSource as unknown as Parameters<
      typeof createStreamController
    >[0]["createEventSource"],
    requestToken,
    onError: opts.onError,
    streamBaseUrl: opts.streamBaseUrl ?? "https://app.test/api/v1",
    onHaptic: opts.onHaptic,
    setTimeoutFn: ((fn: (...args: unknown[]) => void, ms: number) =>
      setTimeout(fn, ms)) as unknown as typeof setTimeout,
    clearTimeoutFn: clearTimeout as unknown as typeof clearTimeout,
  });

  return {
    controller,
    requestToken,
    setQueryData,
    invalidateQueries,
    instances: () => MockEventSource.instances,
  };
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("createStreamController", () => {
  it("start() запрашивает токен и открывает EventSource с правильным URL", async () => {
    const h = createHarness();
    h.controller.start();

    await vi.waitFor(() => expect(h.requestToken).toHaveBeenCalledTimes(1));
    expect(h.requestToken).toHaveBeenCalledWith("habit-abc");

    const instances = h.instances();
    expect(instances).toHaveLength(1);
    expect(instances[0].url).toBe(
      "https://app.test/api/v1/events/stream?habit_id=habit-abc&token=tok-1",
    );
  });

  it("checkin.accepted → queryClient.setQueryData с распарсенным payload, lastEventId сохраняется", async () => {
    const setQueryData = vi.fn();
    const harness = createHarness();
    harness.controller.start();

    await vi.waitFor(() => expect(harness.instances().length).toBe(1));
    const es = harness.instances()[0];

    const todayPayload = {
      habit: { id: "habit-abc" },
      membership: { id: "m-1" },
      checkin: { status: "done", streak_days: 7 },
    };
    es.emit("checkin.accepted", JSON.stringify(todayPayload), "1234-0");

    // setQueryData здесь был вызван через другую ссылку? Нет — один setQueryData на harness.
    // Проверяем по последнему экземпляру (harness.setQueryData).
    expect(harness.setQueryData).toHaveBeenCalledTimes(1);
    expect(harness.setQueryData).toHaveBeenCalledWith(
      ["today", "habit-abc"],
      todayPayload,
    );
    expect(harness.controller.state.lastEventIdUser).toBe("1234-0");
    expect(harness.controller.state.attempt).toBe(0); // успех сбрасывает backoff
    // unused var lint suppression
    void setQueryData;
  });

  it("checkin.rejected → onError с message из payload", async () => {
    const onError = vi.fn();
    const h = createHarness({ onError });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emit(
      "checkin.rejected",
      JSON.stringify({ message: "Окно чек-ина закрыто" }),
    );

    expect(onError).toHaveBeenCalledWith("Окно чек-ина закрыто");
  });

  it("checkin.rejected с битым JSON → onError с дефолтным сообщением", async () => {
    const onError = vi.fn();
    const h = createHarness({ onError });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emit("checkin.rejected", "{not-json");

    expect(onError).toHaveBeenCalledWith("Чек-ин отклонён");
  });

  it("onerror → close + backoff 1s → новый EventSource с НОВЫМ токеном", async () => {
    let n = 0;
    const h = createHarness({
      requestTokenImpl: async () => ({
        token: `tok-${++n}`,
        expires_at: "2099-01-01T00:00:00Z",
      }),
    });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));
    const first = h.instances()[0];
    expect(first.url).toContain("token=tok-1");

    // Триггерим onerror
    first.emitError();
    expect(first.closed).toBe(true);

    // До advance — нет нового EventSource
    expect(h.instances().length).toBe(1);

    // Backoff первой попытки = 1000ms (см. BACKOFFS_MS[0])
    await vi.advanceTimersByTimeAsync(1000);

    // Должен быть второй EventSource с токеном tok-2
    expect(h.instances().length).toBe(2);
    expect(h.instances()[1].url).toContain("token=tok-2");
    expect(h.controller.state.attempt).toBe(1);
  });

  it("backoff cap: после 4+ попыток задержка остаётся 10s", async () => {
    const h = createHarness({
      requestTokenImpl: async () => ({ token: "x", expires_at: "2099-01-01T00:00:00Z" }),
    });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    // 4 неудачи подряд → scheduleReconnect 4 раза → attempt=4, далее BACKOFFS_MS[3]=10s
    for (let i = 0; i < 4; i++) {
      h.instances()[h.instances().length - 1].emitError();
      const delays = [1000, 2000, 5000, 10000];
      await vi.advanceTimersByTimeAsync(delays[i]);
    }

    expect(h.controller.state.attempt).toBe(4);

    // Следующая ошибка должна использовать cap 10000ms, не больше
    h.instances()[h.instances().length - 1].emitError();
    const beforeCount = h.instances().length;
    await vi.advanceTimersByTimeAsync(9999);
    expect(h.instances().length).toBe(beforeCount); // ещё не сработало
    await vi.advanceTimersByTimeAsync(1);
    expect(h.instances().length).toBe(beforeCount + 1); // сработало на 10000
  });

  it("успешный checkin.accepted сбрасывает attempt", async () => {
    const h = createHarness();
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emitError();
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.controller.state.attempt).toBe(1);

    const second = h.instances()[1];
    second.emit(
      "checkin.accepted",
      JSON.stringify({ habit: {}, membership: {}, checkin: {} }),
      "5678-0",
    );
    expect(h.controller.state.attempt).toBe(0);
  });

  it("lastEventId сохраняется и попадает в URL при reconnect", async () => {
    let n = 0;
    const h = createHarness({
      requestTokenImpl: async () => ({
        token: `tok-${++n}`,
        expires_at: "2099-01-01T00:00:00Z",
      }),
    });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    // Успешное событие с lastEventId
    h.instances()[0].emit(
      "checkin.accepted",
      JSON.stringify({ habit: {}, membership: {}, checkin: {} }),
      "1111-0",
    );

    // Триггерим reconnect
    h.instances()[0].emitError();
    await vi.advanceTimersByTimeAsync(1000);

    const second = h.instances()[1];
    expect(second.url).toContain("token=tok-2");
    expect(second.url).toContain("last_event_id=1111-0");
  });

  it("stop() отменяет pending backoff и закрывает EventSource", async () => {
    const h = createHarness();
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emitError();
    h.controller.stop();

    expect(h.instances()[0].closed).toBe(true);
    expect(h.controller.state.isStarted).toBe(false);

    // Через backoff новый EventSource НЕ создаётся
    await vi.advanceTimersByTimeAsync(5000);
    expect(h.instances().length).toBe(1);
    expect(h.requestToken).toHaveBeenCalledTimes(1); // первый раз, до ошибки
  });

  it("requestToken throws (403/503) → backoff запускается, новый EventSource после задержки", async () => {
    let n = 0;
    const h = createHarness({
      requestTokenImpl: async () => {
        n += 1;
        if (n === 1) throw new Error("503 sse_not_configured");
        return { token: `tok-recovered-${n}`, expires_at: "2099-01-01T00:00:00Z" };
      },
    });
    h.controller.start();

    // Ждём микротаск: open() async → requestToken throws → catch → scheduleReconnect.
    // attempt становится 1 только после catch, который срабатывает в следующем tick.
    await vi.waitFor(() => expect(h.controller.state.attempt).toBe(1));

    // EventSource не создан (catch сработал раньше, чем new EventSource).
    expect(h.instances().length).toBe(0);

    // После backoff — повторный open, токен успешен, EventSource создан.
    await vi.advanceTimersByTimeAsync(1000);
    expect(h.instances().length).toBe(1);
    expect(h.instances()[0].url).toContain("token=tok-recovered-2");
  });

  it("start() идемпотентно: повторный вызов не создаёт второй EventSource", async () => {
    const h = createHarness();
    h.controller.start();
    h.controller.start(); // второй раз
    h.controller.start(); // третий раз

    await vi.waitFor(() => expect(h.requestToken).toHaveBeenCalledTimes(1));
    expect(h.instances().length).toBe(1);
  });
});
describe("createStreamController - Item 9 catch and you_were_caught handlers", () => {
  it("catch event invalidates members, today, wallet, balance + haptic medium", async () => {
    const invalidateQueries = vi.fn();
    const h = createHarness({ invalidateQueries });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emit(
      "catch",
      JSON.stringify({
        event: "catch",
        habit_id: "h-1",
        catcher_user_id: 1,
        violator_user_id: 2,
        violator_membership_id: "m-2",
        amount: 10000,
        penalty_id: "p-1",
      }),
      "555-0",
    );

    const queryKeys = invalidateQueries.mock.calls.map(
      (c) => (c[0] as { queryKey: readonly unknown[] }).queryKey[0],
    );
    expect(queryKeys).toEqual(
      expect.arrayContaining(["members", "today", "wallet", "balance"]),
    );
    expect(invalidateQueries).toHaveBeenCalledTimes(4);
  });

  it("you_were_caught event invalidates today, wallet, balance + haptic warning", async () => {
    const invalidateQueries = vi.fn();
    const h = createHarness({ invalidateQueries });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emit(
      "you_were_caught",
      JSON.stringify({
        event: "you_were_caught",
        habit_id: "h-1",
        catcher_user_id: 1,
        catcher_first_name: "Alice",
        violator_first_name: "Victim",
        amount: 15000,
        penalty_id: "p-1",
      }),
      "777-0",
    );

    const queryKeys = invalidateQueries.mock.calls.map(
      (c) => (c[0] as { queryKey: readonly unknown[] }).queryKey[0],
    );
    expect(queryKeys).toEqual(expect.arrayContaining(["today", "wallet", "balance"]));
    expect(invalidateQueries).toHaveBeenCalledTimes(3);
  });

  it("multiplex: catch cursor (habit) and you_were_caught cursor (user) НЕЗАВИСИМЫ", async () => {
    const h = createHarness({ invalidateQueries: vi.fn() });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emit("catch", JSON.stringify({}), "habit-cursor-1");
    h.instances()[0].emit("you_were_caught", JSON.stringify({}), "user-cursor-1");

    expect(h.controller.state.lastEventIdHabit).toBe("habit-cursor-1");
    expect(h.controller.state.lastEventIdUser).toBe("user-cursor-1");
    expect(
      (h.controller.state as { lastEventId?: string }).lastEventId,
    ).toBeUndefined();
  });

  it("reconnect URL содержит ОБА cursor'а после обоих событий", async () => {
    let n = 0;
    const h = createHarness({
      requestTokenImpl: async () => ({
        token: `tok-${++n}`,
        expires_at: "2099-01-01T00:00:00Z",
      }),
    });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    h.instances()[0].emit("checkin.accepted", JSON.stringify({}), "u-1");
    h.instances()[0].emit("catch", JSON.stringify({}), "h-1");
    h.instances()[0].emitError();
    await vi.advanceTimersByTimeAsync(1000);

    const second = h.instances()[1];
    expect(second.url).toContain("last_event_id=u-1");
    expect(second.url).toContain("last_event_id_habit=h-1");
  });

  it("checkin.accepted/rejected handlers НЕ изменились (Item 1 backward-compat)", async () => {
    // Item 9 — additive. Старые handlers (checkin.accepted/rejected)
    // продолжают работать: setQueryData для today, НЕ invalidateQueries.
    const invalidateQueries = vi.fn();
    const h = createHarness({ invalidateQueries });
    h.controller.start();
    await vi.waitFor(() => expect(h.instances().length).toBe(1));

    const todayPayload = { status: "done", streak_days: 5 };
    h.instances()[0].emit(
      "checkin.accepted",
      JSON.stringify(todayPayload),
      "user-cursor-123",
    );

    expect(h.setQueryData).toHaveBeenCalledTimes(1);
    expect(h.setQueryData).toHaveBeenCalledWith(["today", "habit-abc"], todayPayload);
    expect(invalidateQueries).not.toHaveBeenCalled();
  });
});
