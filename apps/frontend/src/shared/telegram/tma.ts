import { init } from "@telegram-apps/sdk";

let initialized = false;

declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        initData: string;
        initDataUnsafe?: {
          user?: {
            id: number;
            first_name?: string;
            last_name?: string;
            username?: string;
            language_code?: string;
            is_premium?: boolean;
          };
        };
        ready: () => void;
        expand: () => void;
        close: () => void;
        BackButton?: { show: () => void; hide: () => void; onClick: (cb: () => void) => void; offClick: (cb: () => void) => void };
        MainButton?: { text: string; show: () => void; hide: () => void; onClick: (cb: () => void) => void; offClick: (cb: () => void) => void };
        HapticFeedback?: {
          impactOccurred: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
          notificationOccurred: (type: "error" | "success" | "warning") => void;
        };
        colorScheme?: "light" | "dark";
        themeParams?: Record<string, string>;
      };
    };
  }
}

export async function initTelegram(): Promise<boolean> {
  if (initialized) return Boolean(window.Telegram?.WebApp);
  if (typeof window === "undefined") return false;
  try {
    await init();
    window.Telegram?.WebApp?.ready?.();
    window.Telegram?.WebApp?.expand?.();
    initialized = true;
    return true;
  } catch {
    initialized = true;
    return false;
  }
}

export function isTelegram(): boolean {
  return Boolean(window.Telegram?.WebApp?.initData);
}

export function getInitData(): string {
  return window.Telegram?.WebApp?.initData ?? "";
}

export function getUser() {
  return window.Telegram?.WebApp?.initDataUnsafe?.user;
}

export function hapticImpact(style: "light" | "medium" | "heavy" = "light"): void {
  window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
}

export function hapticNotify(type: "error" | "success" | "warning"): void {
  window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred(type);
}
