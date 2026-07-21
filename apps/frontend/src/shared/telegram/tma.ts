import { init } from "@telegram-apps/sdk";

let initialized = false;

export async function initTelegram(): Promise<void> {
  if (initialized) return;
  if (typeof window === "undefined") return;
  try {
    await init();
    initialized = true;
  } catch {
    // В dev вне Telegram init() может бросить — продолжаем.
    initialized = true;
  }
}

export function getInitData(): string {
  const tg = (window as unknown as { Telegram?: { WebApp?: { initData?: string } } })
    .Telegram?.WebApp;
  return tg?.initData ?? "";
}

export function hapticImpact(style: "light" | "medium" | "heavy" = "light"): void {
  const tg = (window as unknown as {
    Telegram?: { WebApp?: { HapticFeedback?: { impactOccurred: (s: string) => void } } };
  }).Telegram?.WebApp;
  tg?.HapticFeedback?.impactOccurred(style);
}