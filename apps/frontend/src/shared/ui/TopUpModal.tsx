import { useEffect } from "react";
import { hapticImpact } from "@/shared/telegram/tma";

interface TopUpModalProps {
  open: boolean;
  onClose: () => void;
  currentBalance: number;
}

const PRESETS = [299, 599, 999, 1999];

export function TopUpModal({ open, onClose, currentBalance }: TopUpModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handlePick = (amount: number) => {
    hapticImpact("medium");
    alert(`Пополнение на ${amount} ₽ скоро будет доступно. Сейчас баланс: ${currentBalance} ₽.`);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="topup-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-2xl bg-canvas p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="topup-title" className="text-lg font-bold text-text">
            Пополнить депозит
          </h2>
          <button
            onClick={onClose}
            aria-label="Закрыть"
            className="flex h-9 w-9 items-center justify-center rounded-full text-muted transition hover:bg-surface hover:text-text"
          >
            ✕
          </button>
        </div>

        <p className="mb-4 text-sm text-muted">
          Сумма пойдёт на депозит. Если пропустишь день — штраф спишется сначала отсюда.
        </p>

        <div className="grid grid-cols-2 gap-2">
          {PRESETS.map((amount) => (
            <button
              key={amount}
              onClick={() => handlePick(amount)}
              className="rounded-card border border-white/5 bg-surface p-4 text-center transition active:scale-95 hover:border-primary/40"
            >
              <div className="text-lg font-bold text-text">{amount} ₽</div>
              <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">
                ≈ {Math.floor(amount / 30)} дней
              </div>
            </button>
          ))}
        </div>

        <div className="mt-4 rounded-card border border-primary/20 bg-primary/5 p-3 text-xs text-muted">
          💳 <strong className="text-text">Скоро:</strong> СБП, банковские карты, Telegram Stars.
        </div>
      </div>
    </div>
  );
}
