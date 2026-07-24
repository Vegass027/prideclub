import { useEffect, useState } from "react";
import { useTopUpDeposit } from "@/shared/hooks";
import { hapticImpact, hapticNotify, showAlert } from "@/shared/telegram/tma";
import type { Habit } from "@/shared/types";

interface TopUpModalProps {
  open: boolean;
  onClose: () => void;
  habits: Habit[];
}

const PRESETS_KOPECKS = [299 * 100, 599 * 100, 999 * 100, 1999 * 100];

const KOPECKS_PER_RUB = 100;

const formatRub = (kopecks: number): string =>
  `${Math.round(kopecks / KOPECKS_PER_RUB).toLocaleString("ru-RU")} ₽`;

export function TopUpModal({ open, onClose, habits }: TopUpModalProps) {
  const topup = useTopUpDeposit();
  const [selectedHabitId, setSelectedHabitId] = useState<string>("");

  useEffect(() => {
    if (!open) return;
    if (habits.length > 0 && selectedHabitId === "") {
      setSelectedHabitId(habits[0].id);
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, habits, selectedHabitId]);

  if (!open) return null;

  const handlePick = (amountKopecks: number) => {
    if (!selectedHabitId) return;
    hapticImpact("medium");
    topup.mutate(
      { habit_id: selectedHabitId, amount_kopecks: amountKopecks },
      {
        onSuccess: (data) => {
          if (data.ok) {
            hapticNotify("success");
            void showAlert(
              `Зачислено ${formatRub(amountKopecks)}. Новый баланс: ${formatRub(data.new_deposit_balance ?? 0)}.`,
            );
            onClose();
          } else {
            hapticNotify("error");
            void showAlert(`Не удалось пополнить: ${data.code ?? "ошибка"}.`);
          }
        },
        onError: () => {
          hapticNotify("error");
          void showAlert("Сетевая ошибка. Попробуй ещё раз.");
        },
      },
    );
  };

  const isPending = topup.isPending;

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
            disabled={isPending}
            className="flex h-9 w-9 items-center justify-center rounded-full text-muted transition hover:bg-surface hover:text-text disabled:opacity-40"
          >
            ✕
          </button>
        </div>

        <p className="mb-4 text-sm text-muted">
          Сумма пойдёт на депозит. Если пропустишь день — штраф спишется сначала отсюда.
        </p>

        {habits.length > 1 && (
          <fieldset className="mb-4">
            <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
              В какой клуб
            </legend>
            <div className="space-y-1">
              {habits.map((h) => (
                <label
                  key={h.id}
                  className={`flex cursor-pointer items-center gap-2 rounded-card border px-3 py-2 text-sm transition ${
                    selectedHabitId === h.id
                      ? "border-primary/60 bg-primary/10 text-text"
                      : "border-white/5 bg-surface text-muted hover:border-white/20"
                  }`}
                >
                  <input
                    type="radio"
                    name="topup-habit"
                    value={h.id}
                    checked={selectedHabitId === h.id}
                    onChange={() => setSelectedHabitId(h.id)}
                    className="sr-only"
                    disabled={isPending}
                  />
                  <span aria-hidden="true">
                    {selectedHabitId === h.id ? "●" : "○"}
                  </span>
                  <span className="truncate">〖{h.title}〗</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div className="grid grid-cols-2 gap-2">
          {PRESETS_KOPECKS.map((amount) => (
            <button
              key={amount}
              type="button"
              onClick={() => handlePick(amount)}
              disabled={isPending || !selectedHabitId}
              className="rounded-card border border-white/5 bg-surface p-4 text-center transition active:scale-95 hover:border-primary/40 disabled:opacity-40"
            >
              <div className="text-lg font-bold text-text">
                {formatRub(amount)}
              </div>
              <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">
                ≈ {Math.floor(amount / KOPECKS_PER_RUB / 30)} дней
              </div>
            </button>
          ))}
        </div>

        {isPending && (
          <p className="mt-3 text-center text-xs text-muted">Зачисляю…</p>
        )}

        <div className="mt-4 rounded-card border border-primary/20 bg-primary/5 p-3 text-xs text-muted">
          💳 <strong className="text-text">Скоро:</strong> СБП, банковские карты, Telegram Stars.
        </div>
      </div>
    </div>
  );
}
