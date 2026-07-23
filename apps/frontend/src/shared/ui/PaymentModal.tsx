import { useEffect, useState } from "react";
import { hapticImpact, hapticNotify } from "@/shared/telegram/tma";
import type { Habit } from "@/shared/types";

interface PaymentModalProps {
  habit: Habit | null;
  onClose: () => void;
  onSuccess: (habit: Habit) => void;
}

export function PaymentModal({ habit, onClose, onSuccess }: PaymentModalProps) {
  const [step, setStep] = useState<"review" | "processing" | "success">("review");

  useEffect(() => {
    if (habit) setStep("review");
  }, [habit?.id]);

  useEffect(() => {
    if (!habit) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && step !== "processing") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [habit, step, onClose]);

  if (!habit) return null;

  const handlePay = async () => {
    hapticImpact("heavy");
    setStep("processing");
    await new Promise((r) => setTimeout(r, 1200));
    hapticNotify("success");
    setStep("success");
    setTimeout(() => {
      onSuccess(habit);
      onClose();
    }, 800);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="payment-title"
      onClick={() => step !== "processing" && onClose()}
    >
      <div
        className="w-full max-w-md rounded-t-2xl bg-canvas p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="payment-title" className="text-lg font-bold text-text">
            Вступление в клуб
          </h2>
          <button
            onClick={onClose}
            disabled={step === "processing"}
            aria-label="Закрыть"
            className="flex h-9 w-9 items-center justify-center rounded-full text-muted transition hover:bg-surface hover:text-text disabled:opacity-30"
          >
            ✕
          </button>
        </div>

        {step === "review" && (
          <>
            <section className="mb-4 rounded-card border border-white/5 bg-surface p-4">
              <h3 className="text-base font-semibold text-text">{habit.title}</h3>
              {habit.description && (
                <p className="mt-1 line-clamp-3 text-xs text-muted">{habit.description}</p>
              )}
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-muted">Подписка</dt>
                  <dd className="text-base font-bold text-text">
                    {(habit.price_month / 100).toFixed(2)} ₽/мес
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-muted">Штраф</dt>
                  <dd className="text-base font-bold text-danger">
                    {(habit.penalty_amount / 100).toFixed(2)} ₽
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-muted">Окно</dt>
                  <dd className="text-sm font-semibold text-text">
                    {habit.checkin_window_start.slice(0, 5)}–{habit.checkin_window_end.slice(0, 5)}
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-muted">Участников</dt>
                  <dd className="text-sm font-semibold text-text">{habit.members_count}</dd>
                </div>
              </dl>
            </section>

            <div className="mb-3 rounded-card border border-primary/20 bg-primary/5 p-3 text-xs text-muted">
              ℹ️ <strong className="text-text">Мок оплата.</strong> Сейчас платёжный шлюз
              не подключён — кнопка просто зачислит подписку. Реальный провайдер
              (СБП / Telegram Stars) появится в ближайшем релизе.
            </div>

            <button
              type="button"
              onClick={handlePay}
              className="w-full rounded-card bg-primary py-3 text-sm font-semibold text-white shadow-card transition active:scale-[0.98] hover:opacity-90"
            >
              Оплатить {(habit.price_month / 100).toFixed(2)} ₽ и вступить
            </button>
          </>
        )}

        {step === "processing" && (
          <div className="flex flex-col items-center justify-center py-12">
            <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent" />
            <p className="mt-4 text-sm font-semibold text-text">Обрабатываем платёж...</p>
            <p className="mt-1 text-xs text-muted">Не закрывайте окно</p>
          </div>
        )}

        {step === "success" && (
          <div className="flex flex-col items-center justify-center py-12">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-success/20 text-3xl text-success">
              ✓
            </div>
            <p className="mt-4 text-base font-bold text-text">Готово!</p>
            <p className="mt-1 text-sm text-muted">Открываем клуб...</p>
          </div>
        )}
      </div>
    </div>
  );
}
