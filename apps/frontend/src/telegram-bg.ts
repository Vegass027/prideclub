// Сайд-эффект: при загрузке модуля фиксируем тёмный фон Mini App.
//
// Импортируется как `import "./telegram-bg"` из App.tsx, поэтому
// Rollup/Vite гарантированно включает его в бандл (не вырежет как
// dead-code, потому что side-effect модуля сохраняются).
//
// Доступ к методам SDK через динамические ключи, чтобы tree-shaking
// не оптимизировал вызовы как `no-op`.

if (typeof window !== "undefined") {
  const win = window as unknown as {
    Telegram?: { WebApp?: Record<string, unknown> };
  };
  const tg = win.Telegram?.WebApp;
  if (tg) {
    const tryCall = (key: string) => {
      const fn = tg[key];
      if (typeof fn === "function") {
        try {
          (fn as (c: string) => void)("#0F1115");
        } catch {
          // ignore — старые версии SDK могут бросать
        }
      }
    };
    tryCall("setHeaderColor");
    tryCall("setBackgroundColor");
    tryCall("setBottomBarColor");
  }
}

export {};