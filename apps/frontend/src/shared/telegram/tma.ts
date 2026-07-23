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
            photo_url?: string;
          };
        };
        ready: () => void;
        expand: () => void;
        close: () => void;
        openLink?: (url: string, tryInstantView?: boolean) => void;
        openTelegramLink?: (url: string) => void;
        BackButton?: { show: () => void; hide: () => void; onClick: (cb: () => void) => void; offClick: (cb: () => void) => void };
        MainButton?: { text: string; show: () => void; hide: () => void; onClick: (cb: () => void) => void; offClick: (cb: () => void) => void };
        HapticFeedback?: {
          impactOccurred: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
          notificationOccurred: (type: "error" | "success" | "warning") => void;
        };
        setHeaderColor?: (color: string) => void;
        setBackgroundColor?: (color: string) => void;
        setBottomBarColor?: (color: string) => void;
        colorScheme?: "light" | "dark";
        themeParams?: Record<string, string>;
      };
    };
  }
}

// Прямой side-effect: фиксируем тёмный фон Mini App сразу при
// загрузке модуля. Tree-shaking не уберёт это, потому что мы
// пишем в window (site-effect очевиден для bundler'а).
if (typeof window !== "undefined") {
  const tg = (
    window as unknown as {
      Telegram?: { WebApp?: Record<string, unknown> };
    }
  ).Telegram?.WebApp;
  if (tg) {
    const tryCall = (key: string) => {
      const fn = tg[key];
      if (typeof fn === "function") {
        try {
          (fn as (c: string) => void)("#0F1115");
        } catch {
          // ignore — старые версии SDK
        }
      }
    };
    tryCall("setHeaderColor");
    tryCall("setBackgroundColor");
    tryCall("setBottomBarColor");
  }
}

export async function initTelegram(): Promise<boolean> {
  if (initialized) return Boolean(window.Telegram?.WebApp);
  if (typeof window === "undefined") return false;
  try {
    await init();
    const webapp = window.Telegram?.WebApp;
    webapp?.ready?.();
    webapp?.expand?.();
    // Фиксируем тёмный фон Mini App, чтобы до отрисовки React и
    // на устройствах с белой темой Telegram фон не моргал белым.
    // Telegram WebApp SDK >=6.0 поддерживает setBackgroundColor.
    try {
      webapp?.setHeaderColor?.("#0F1115");
      webapp?.setBackgroundColor?.("#0F1115");
      webapp?.setBottomBarColor?.("#0F1115");
    } catch {
      // ignore — старые версии SDK
    }
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

export function getUserPhoto(): string | null {
  return window.Telegram?.WebApp?.initDataUnsafe?.user?.photo_url ?? null;
}

export function hapticImpact(style: "light" | "medium" | "heavy" = "light"): void {
  window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
}

export function hapticNotify(type: "error" | "success" | "warning"): void {
  window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred(type);
}

export function openTelegramLink(url: string): void {
  const tg = window.Telegram?.WebApp;
  if (tg?.openTelegramLink) {
    tg.openTelegramLink(url);
    return;
  }
  if (tg?.openLink) {
    tg.openLink(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}
