import { hapticImpact, hapticNotify } from "@/shared/telegram/tma";
import { missingKopecks } from "@/shared/utils/topupPresets";

interface InsufficientDepositModalProps {
  open: boolean;
  onClose: () => void;
  /** required_kopecks из InsufficientDepositError (penalty клуба). */
  requiredKopecks: number;
  /** current_kopecks из InsufficientDepositError (текущий депозит юзера). */
  currentKopecks: number;
  /** Название клуба — для контекста в тексте. */
  habitTitle?: string;
  /** Колбэк "Пополнить" — открывает TopUpModal с defaultAmount. */
  onTopUp: () => void;
}

const KOPECKS_PER_RUB = 100;
const formatRub = (kopecks: number): string =>
  `${Math.round(kopecks / KOPECKS_PER_RUB).toLocaleString("ru-RU")} ₽`;

export function InsufficientDepositModal({
  open,
  onClose,
  requiredKopecks,
  currentKopecks,
  habitTitle,
  onTopUp,
}: InsufficientDepositModalProps) {
  if (!open) return null;

  const missing = missingKopecks(requiredKopecks, currentKopecks);

  const handleTopUp = () => {
    hapticImpact("medium");
    hapticNotify("warning");
    onTopUp();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="insufficient-deposit-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-2xl bg-canvas p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2
            id="insufficient-deposit-title"
            className="text-lg font-bold text-text"
          >
            Недостаточно средств
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
          {habitTitle ? (
            <>
              Для вступления в клуб <strong className="text-text">«{habitTitle}»</strong>{" "}
              нужно <strong className="text-text">{formatRub(requiredKopecks)}</strong> на депозите.
            </>
          ) : (
            <>
              Для вступления нужно{" "}
              <strong className="text-text">{formatRub(requiredKopecks)}</strong> на депозите.
            </>
          )}
        </p>

        <div className="mb-4 grid grid-cols-2 gap-3 rounded-card border border-white/5 bg-surface p-3 text-sm">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted">
              Сейчас
            </div>
            <div className="text-base font-semibold text-text">
              {formatRub(currentKopecks)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted">
              Не хватает
            </div>
            <div className="text-base font-semibold text-danger">
              {formatRub(missing)}
            </div>
          </div>
        </div>

        <button
          type="button"
          onClick={handleTopUp}
          className="w-full rounded-card bg-primary px-4 py-3 text-sm font-semibold text-white transition active:scale-95"
        >
          Пополнить на {formatRub(missing)}
        </button>

        <button
          type="button"
          onClick={onClose}
          className="mt-2 w-full rounded-card border border-white/10 bg-transparent px-4 py-3 text-sm font-medium text-muted transition hover:text-text"
        >
          Отмена
        </button>
      </div>
    </div>
  );
}
