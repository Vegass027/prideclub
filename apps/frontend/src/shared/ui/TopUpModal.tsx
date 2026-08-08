import { useEffect, useState } from "react";
import { useTopUpDeposit } from "@/shared/hooks";
import { hapticImpact, hapticNotify, showAlert } from "@/shared/telegram/tma";
import {
  DEFAULT_TOPUP_PRESETS_KOPECKS,
  pickPresetToCover,
} from "@/shared/utils/topupPresets";

interface TopUpModalProps {
  open: boolean;
  onClose: () => void;
  /**
   * Pravki-deposit-sse.md §Z-3.4: предзаполнение суммы пополнения.
   * Используется из InsufficientDepositModal — сумма, которой не хватает
   * для вступления. Если передано — UI подсвечивает наименьший пресет,
   * который покрывает defaultAmount (или показывает "своя сумма" input,
   * если defaultAmount > max пресета).
   */
  defaultAmount?: number;
}

const KOPECKS_PER_RUB = 100;

const formatRub = (kopecks: number): string =>
  `${Math.round(kopecks / KOPECKS_PER_RUB).toLocaleString("ru-RU")} ₽`;

export function TopUpModal({ open, onClose, defaultAmount }: TopUpModalProps) {
  const topup = useTopUpDeposit();
  const [customAmount, setCustomAmount] = useState<string>("");

  // Подсвеченный пресет — если defaultAmount передан и покрывается пресетом,
  // иначе null (= "своя сумма" режим).
  const recommendedPreset = defaultAmount
    ? pickPresetToCover(defaultAmount, DEFAULT_TOPUP_PRESETS_KOPECKS)
    : null;
  const showCustomInput =
    defaultAmount !== undefined && recommendedPreset === null;

  // Предзаполняем custom input при входе в custom-режим.
  useEffect(() => {
    if (open && showCustomInput && customAmount === "") {
      setCustomAmount(String(Math.round(defaultAmount! / KOPECKS_PER_RUB)));
    }
    if (!open) {
      setCustomAmount("");
    }
  }, [open, showCustomInput, defaultAmount, customAmount]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handlePick = (amountKopecks: number) => {
    if (amountKopecks <= 0) return;
    hapticImpact("medium");
    // PR #2: backend ещё принимает habit_id (backward-compat) для legacy
    // membership-create на API уровне. Не шлём его — backend сам определит
    // membership по user. Передаём пустой habit_id, бэкенд проигнорирует.
    topup.mutate(
      { habit_id: "", amount_kopecks: amountKopecks },
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

  const handleCustomSubmit = () => {
    const rub = parseInt(customAmount, 10);
    if (Number.isNaN(rub) || rub <= 0) {
      void showAlert("Введите сумму больше 0.");
      return;
    }
    handlePick(rub * KOPECKS_PER_RUB);
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

        {defaultAmount !== undefined && (
          <p className="mb-4 rounded-card border border-primary/30 bg-primary/10 p-3 text-xs text-text">
            Рекомендуем пополнить на{" "}
            <strong className="text-primary">{formatRub(defaultAmount)}</strong>{" "}
            — столько не хватает для вступления.
          </p>
        )}

        {showCustomInput ? (
          <div className="mb-4">
            <label
              htmlFor="topup-custom"
              className="mb-2 block text-xs font-semibold uppercase tracking-wide text-muted"
            >
              Своя сумма (₽)
            </label>
            <div className="flex gap-2">
              <input
                id="topup-custom"
                type="number"
                inputMode="numeric"
                min="1"
                value={customAmount}
                onChange={(e) => setCustomAmount(e.target.value)}
                disabled={isPending}
                className="flex-1 rounded-card border border-white/10 bg-surface px-3 py-3 text-base text-text focus:border-primary focus:outline-none disabled:opacity-40"
                placeholder="например, 1500"
              />
              <button
                type="button"
                onClick={handleCustomSubmit}
                disabled={isPending || customAmount === ""}
                className="rounded-card bg-primary px-5 py-3 text-sm font-semibold text-white transition active:scale-95 disabled:opacity-40"
              >
                Пополнить
              </button>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {DEFAULT_TOPUP_PRESETS_KOPECKS.map((amount) => {
              const isRecommended = recommendedPreset === amount;
              return (
                <button
                  key={amount}
                  type="button"
                  onClick={() => handlePick(amount)}
                  disabled={isPending}
                  className={`rounded-card border p-4 text-center transition active:scale-95 disabled:opacity-40 ${
                    isRecommended
                      ? "border-primary bg-primary/15 shadow-[0_0_0_2px_rgba(99,102,241,0.4)]"
                      : "border-white/5 bg-surface hover:border-primary/40"
                  }`}
                >
                  <div className="text-lg font-bold text-text">
                    {formatRub(amount)}
                  </div>
                  <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">
                    ≈ {Math.floor(amount / KOPECKS_PER_RUB / 30)} дней
                  </div>
                  {isRecommended && (
                    <div className="mt-1 text-[10px] font-semibold uppercase tracking-wide text-primary">
                      рекомендуем
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}

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
