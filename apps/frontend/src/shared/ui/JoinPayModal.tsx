import { useEffect, useState } from "react";
import { useJoinAndPay } from "@/shared/hooks";
import { hapticImpact, hapticNotify, showAlert } from "@/shared/telegram/tma";
import {
  DEFAULT_TOPUP_PRESETS_KOPECKS,
  pickPresetToCover,
} from "@/shared/utils/topupPresets";

/**
 * Pravki-subscribe-and-join.md §Z-16: модалка оплаты при первом вступлении в клуб.
 *
 * Два режима (выбираются JoinButton через prop `mode`):
 * - `"full"` — новое вступление или истёкшая подписка. Показываем чекбокс
 *   согласия на подписку + пресеты депозита. Кнопка «Оплатить {total} ₽».
 * - `"deposit-only"` — у юзера есть активная подписка (existing.subscription_until
 *   >= today на момент pre-check в JoinButton). Чекбокс НЕ показывается,
 *   `subscription_accepted: false` в payload. Кнопка «Пополнить {deposit} ₽
 *   и открыть клуб».
 *
 * Pre-check на бэкенде (§Z-13.1 матрица) defensive-валидирует: даже если
 * frontend ошибся с mode, спишется правильная сумма, `charged_subscription`
 * flag в response покажет что реально списали.
 *
 * Persistence — через БД, не через localStorage: после успеха юзер попадает
 * на /today, фронт видит ACTIVE membership через стандартный fetch.
 */
export type JoinPayModalMode = "full" | "deposit-only" | "renew-only";

interface JoinPayModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  /** Карточка клуба — id, title, penalty_amount, price_month. */
  habit: {
    id: string;
    title: string;
    penalty_amount: number;
    price_month: number;
  };
  /** Режим модалки (см. JoinPayModalMode). Z-17 выбирает на основе myHabits. */
  mode: JoinPayModalMode;
  /**
   * Subtitle в шапке модалки (например, "Вступить в клуб" или "Пополнить и открыть клуб").
   * Если не передан — выбирается по mode.
   */
  subtitle?: string;
}

const KOPECKS_PER_RUB = 100;

const formatRub = (kopecks: number): string =>
  `${Math.round(kopecks / KOPECKS_PER_RUB).toLocaleString("ru-RU")} ₽`;

/**
 * Генерация UUID4 для idempotency_key. На проде используется crypto.randomUUID()
 * (доступен во всех evergreen браузерах и Telegram WebView). На старых —
 * fallback на Math.random-based UUID v4 (RFC 4122 §4.4).
 */
function uuid4(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Маппинг ошибок бэкенда на русские тексты для пользователя.
 * Pravki-subscribe-and-join.md §Z-16.4 — `code` из InsufficientDepositError-style response.
 */
function formatErrorMessage(code: string | undefined): string {
  switch (code) {
    case "habit_not_found":
      return "Клуб не найден.";
    case "habit_archived":
      return "Клуб архивирован.";
    case "habit_inactive":
      return "Клуб сейчас неактивен.";
    case "already_active":
      return "Ты уже в клубе. Обнови страницу.";
    case "insufficient_deposit_choice":
      return "Выбранная сумма меньше штрафа клуба. Выбери ≥ порога.";
    case "subscription_required":
      return "Нужно согласие на подписку.";
    case "idempotency_conflict":
      return "Ошибка оплаты. Попробуй ещё раз.";
    default:
      return "Не удалось вступить. Попробуй ещё раз.";
  }
}

/**
 * UI-хук для извлечения кода ошибки из ApiError.
 * Импортируется напрямую, чтобы избежать цикла с client.ts.
 */
type ApiErrorLike = {
  data?: { code?: string; message?: string };
};

function extractErrorCode(err: unknown): string | undefined {
  if (typeof err === "object" && err !== null && "data" in err) {
    const data = (err as ApiErrorLike).data;
    if (data && typeof data === "object" && typeof data.code === "string") {
      return data.code;
    }
  }
  return undefined;
}

export function JoinPayModal({
  open,
  onClose,
  onSuccess,
  habit,
  mode,
  subtitle,
}: JoinPayModalProps) {
  const subscribe = useJoinAndPay((data) => {
    // Pravki-subscribe-and-join.md §Z-17 substep 2 (gap fix):
    // Алерт строится на основе РЕАЛЬНО списанной суммы из response,
    // а не предпосчитанной `totalKopecks` в UI. Это критично для случая
    // LEFT + активная подписка (см. §Z-13.1 кейс 3b): UI в режиме "full"
    // показывал "Оплатить X ₽" (price_month + deposit), но бэкенд
    // списал только deposit (charged_subscription=false). Без этого
    // фикса пользователь видел бы одно число, а по факту снялось другое.
    hapticNotify("success");
    const actualRub = Math.round(data.total_charged_kopecks / KOPECKS_PER_RUB);
    const message = data.charged_subscription
      ? `Готово! Списано ${actualRub.toLocaleString("ru-RU")} ₽ (подписка + депозит). Добро пожаловать в клуб.`
      : `Готово! Списано ${actualRub.toLocaleString("ru-RU")} ₽ (только депозит). Подписка активна до ${data.subscription_until}.`;
    void showAlert(message);
    onClose();
    onSuccess?.();
  });

  const [subscriptionAccepted, setSubscriptionAccepted] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState<number | null>(null);
  const [customAmount, setCustomAmount] = useState<string>("");

  // Пресеты фильтруются по penalty_amount: убираются < penalty (защита от UI-багов).
  // Бэкенд всё равно валидирует (422 insufficient_deposit_choice), но фронт
  // не должен давать выбрать заведомо малую сумму.
  const availablePresets = DEFAULT_TOPUP_PRESETS_KOPECKS.filter(
    (p) => p >= habit.penalty_amount,
  );
  const recommendedPreset = pickPresetToCover(
    habit.penalty_amount,
    availablePresets,
  );
  const showCustomInput = availablePresets.length === 0;
  const isFullMode = mode === "full";
  // Pravki-subscription-2026-08-17 §Z-13.5 (commit 1, smart renew):
  // renew-only mode — продление подписки когда deposit уже достаточный.
  // Показываем только price_month, чекбокс подписки НЕ нужен (auto-accept),
  // блок выбора депозита скрыт (отправляем deposit_amount_kopecks=0).
  const isRenewOnlyMode = mode === "renew-only";

  // Подсчёт итогов.
  const chosenDepositKopecks = isRenewOnlyMode
    ? 0
    : selectedPreset ??
      (customAmount ? Math.max(0, parseInt(customAmount, 10)) * KOPECKS_PER_RUB : 0);
  const totalKopecks = isFullMode
    ? habit.price_month + chosenDepositKopecks
    : chosenDepositKopecks;
  const canPay = isRenewOnlyMode
    ? true  // В renew-only всегда можно платить (subscription auto-accepted, deposit=0)
    : chosenDepositKopecks > 0 &&
      chosenDepositKopecks >= habit.penalty_amount &&
      (isFullMode ? subscriptionAccepted : true);

  // Escape закрывает (как в TopUpModal).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !subscribe.isPending) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, subscribe.isPending]);

  // Сброс состояния при закрытии.
  useEffect(() => {
    if (!open) {
      setSubscriptionAccepted(false);
      setSelectedPreset(null);
      setCustomAmount("");
    }
  }, [open]);

  if (!open) return null;

  const handlePresetPick = (amountKopecks: number) => {
    if (amountKopecks <= 0) return;
    hapticImpact("medium");
    setSelectedPreset(amountKopecks);
    setCustomAmount("");
  };

  const handleCustomChange = (value: string) => {
    setCustomAmount(value);
    setSelectedPreset(null);
  };

  const handlePay = () => {
    if (!canPay) return;
    hapticImpact("medium");
    subscribe.mutate(
      {
        habit_id: habit.id,
        deposit_amount_kopecks: chosenDepositKopecks,
        // renew-only: subscription_accepted=true автоматически (нет чекбокса).
        // full mode: требуем явное согласие через чекбокс.
        // deposit-only mode: не нужно (case 3b), но отправляем false (бэкенд
        // проверяет: только при активной подписке можно слать false).
        subscription_accepted: isRenewOnlyMode ? true : isFullMode ? subscriptionAccepted : false,
        idempotency_key: uuid4(),
      },
      {
        onError: (err: unknown) => {
          hapticNotify("error");
          const code = extractErrorCode(err);
          void showAlert(formatErrorMessage(code));
        },
      },
    );
  };

  const titleText =
    subtitle ??
    (isRenewOnlyMode
      ? `Продлить участие в «${habit.title}»`
      : isFullMode
      ? `Вступить в клуб «${habit.title}»`
      : `Пополнить и открыть «${habit.title}»`);

  const buttonText = isRenewOnlyMode
    ? `Оплатить ${formatRub(totalKopecks)}`
    : isFullMode
    ? `Оплатить ${formatRub(totalKopecks)}`
    : `Пополнить ${formatRub(totalKopecks)} и открыть клуб`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="join-pay-title"
      onClick={() => !subscribe.isPending && onClose()}
    >
      <div
        className="w-full max-w-md rounded-t-2xl bg-canvas p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="join-pay-title" className="text-lg font-bold text-text">
            {titleText}
          </h2>
          <button
            onClick={onClose}
            aria-label="Закрыть"
            disabled={subscribe.isPending}
            className="flex h-9 w-9 items-center justify-center rounded-full text-muted transition hover:bg-surface hover:text-text disabled:opacity-40"
          >
            ✕
          </button>
        </div>

        {/* Подписка — в full-режиме (с чекбоксом) или renew-only (auto-accept, без чекбокса). */}
        {(isFullMode || isRenewOnlyMode) && (
          <div className="mb-4 rounded-card border border-white/10 bg-surface p-3">
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">
              Подписка
            </div>
            <div className="mb-3 text-base font-semibold text-text">
              {formatRub(habit.price_month)} / мес
            </div>
            {isFullMode && (
              <label className="flex cursor-pointer items-start gap-2 text-sm text-text">
                <input
                  type="checkbox"
                  checked={subscriptionAccepted}
                  onChange={(e) => {
                    hapticImpact("light");
                    setSubscriptionAccepted(e.target.checked);
                  }}
                  disabled={subscribe.isPending}
                  className="mt-0.5 h-4 w-4 rounded border-white/20 bg-surface text-primary focus:ring-primary disabled:opacity-40"
                />
                <span>
                  Согласен на подписку {formatRub(habit.price_month)} / мес
                </span>
              </label>
            )}
            {isRenewOnlyMode && (
              <p className="mt-1 text-sm text-text">
                Продление подписки на 30 дней.
              </p>
            )}
            <p className="mt-2 text-[11px] leading-snug text-muted">
              Это первый платёж. В следующий раз при повторном открытии клуба
              нужно будет пополнить только депозит.
            </p>
          </div>
        )}

        {/* Блок выбора суммы депозита — пропускаем в renew-only (депозит не трогаем). */}
        {!isRenewOnlyMode && (
          <>
            <div className="mb-3 text-[10px] uppercase tracking-wide text-muted">
              Депозит
            </div>
        {showCustomInput ? (
          <div className="mb-4">
            <label
              htmlFor="join-pay-custom"
              className="mb-2 block text-xs font-semibold uppercase tracking-wide text-muted"
            >
              Своя сумма (₽)
            </label>
            <div className="flex gap-2">
              <input
                id="join-pay-custom"
                type="number"
                inputMode="numeric"
                min="1"
                value={customAmount}
                onChange={(e) => handleCustomChange(e.target.value)}
                disabled={subscribe.isPending}
                className="flex-1 rounded-card border border-white/10 bg-surface px-3 py-3 text-base text-text focus:border-primary focus:outline-none disabled:opacity-40"
                placeholder={`минимум ${Math.round(habit.penalty_amount / KOPECKS_PER_RUB)} ₽`}
              />
            </div>
          </div>
        ) : (
          <div className="mb-4 grid grid-cols-2 gap-2">
            {availablePresets.map((amount) => {
              const isRecommended = recommendedPreset === amount;
              const isSelected = selectedPreset === amount;
              return (
                <button
                  key={amount}
                  type="button"
                  onClick={() => handlePresetPick(amount)}
                  disabled={subscribe.isPending}
                  className={`rounded-card border p-4 text-center transition active:scale-95 disabled:opacity-40 ${
                    isSelected
                      ? "border-primary bg-primary/15 shadow-[0_0_0_2px_rgba(99,102,241,0.4)]"
                      : isRecommended
                      ? "border-primary/40 bg-primary/5"
                      : "border-white/5 bg-surface hover:border-primary/40"
                  }`}
                >
                  <div className="text-lg font-bold text-text">
                    {formatRub(amount)}
                  </div>
                  {isRecommended && (
                    <div className="mt-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                      рекомендуем
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
          </>
        )}

        {/* Информация о депозите в renew-only mode (не трогаем его). */}
        {isRenewOnlyMode && (
          <div className="mb-4 rounded-card border border-success/30 bg-success/10 p-3 text-sm">
            <strong className="block text-success">
              ✓ Депозит уже достаточный
            </strong>
            <span className="mt-1 block text-xs text-muted">
              Списываем только стоимость подписки. Депозит не трогаем.
            </span>
          </div>
        )}

        {/* Итог — для full и renew-only. */}
        {(isFullMode || isRenewOnlyMode) && (
          <div className="mb-4 flex items-baseline justify-between border-t border-white/5 pt-3">
            <span className="text-sm text-muted">Итого к оплате</span>
            <span className="text-lg font-bold text-text">
              {formatRub(totalKopecks)}
            </span>
          </div>
        )}

        <button
          type="button"
          onClick={handlePay}
          disabled={!canPay || subscribe.isPending}
          className="w-full rounded-card bg-primary px-4 py-3 text-sm font-semibold text-white transition active:scale-95 disabled:opacity-40"
        >
          {buttonText}
        </button>

        {subscribe.isPending && (
          <p className="mt-3 text-center text-xs text-muted">Зачисляю…</p>
        )}

        <div className="mt-4 rounded-card border border-primary/20 bg-primary/5 p-3 text-xs text-muted">
          💳 <strong className="text-text">Скоро:</strong> СБП, банковские карты,
          Telegram Stars.
        </div>
      </div>
    </div>
  );
}
