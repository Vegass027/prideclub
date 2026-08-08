import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useJoinHabit } from "@/shared/hooks";
import { ApiError } from "@/shared/api/client";
import { Button } from "@/shared/ui/Button";
import { InsufficientDepositModal } from "@/shared/ui/InsufficientDepositModal";
import { TopUpModal } from "@/shared/ui/TopUpModal";
import { hapticImpact, hapticNotify } from "@/shared/telegram/tma";
import type { Habit } from "@/shared/types";

interface JoinButtonProps {
  habit: Habit;
  /**
   * Pravki-deposit-sse.md §Z-3.3: если уже известно, что юзер
   * не имеет membership (например, из useMyHabits), можно отключить кнопку.
   * Сейчас НЕ используется (используется 403 handler), но оставлен
   * для будущей оптимизации (например, блокировать кнопку заранее если
   * уже состоит в клубе).
   */
  isAlreadyJoined?: boolean;
}

/**
 * Pravki-deposit-sse.md §Z-3.5: компонент кнопки «Вступить» в клубе.
 *
 * Извлечён из MarketplacePage для тестируемости (план Z-3.5 требовал
 * JoinButton.test.tsx — без извлечения тестировать пришлось бы всю
 * MarketplacePage, что тянет за собой мок роутера, мок Marketplace useQuery,
 * и т.д.).
 *
 * Поведение:
 * - Клик → useJoinHabit.mutate(habit.id)
 * - 200 OK → navigate(/habits/{id}/today) БЕЗ window.location.reload()
 *   (важно: это ключевое UX-требование PR #2 — клик вступить НЕ перезагружает страницу)
 * - 403 insufficient_deposit → InsufficientDepositModal с кнопкой «Пополнить»
 *   → TopUpModal с defaultAmount = required - current
 * - Другие ошибки → alert + haptic error
 */
export function JoinButton({ habit, isAlreadyJoined = false }: JoinButtonProps) {
  const navigate = useNavigate();
  const joinMutation = useJoinHabit();
  const [depositModal, setDepositModal] = useState<{
    requiredKopecks: number;
    currentKopecks: number;
  } | null>(null);
  const [topupOpen, setTopupOpen] = useState(false);

  const handleClick = () => {
    hapticImpact("medium");
    joinMutation.mutate(habit.id, {
      onSuccess: () => {
        hapticNotify("success");
        // БЕЗ window.location.reload() — navigate через React Router,
        // state кеша обновится через onSuccess invalidateQueries в useJoinHabit.
        navigate(`/habits/${habit.id}/today`);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.data?.code === "insufficient_deposit") {
          const requiredKopecks =
            typeof err.data.required_kopecks === "number"
              ? err.data.required_kopecks
              : habit.penalty_amount;
          const currentKopecks =
            typeof err.data.current_kopecks === "number"
              ? err.data.current_kopecks
              : 0;
          setDepositModal({ requiredKopecks, currentKopecks });
          hapticNotify("warning");
          return;
        }
        hapticNotify("error");
        alert("Не удалось зачислить подписку. Попробуй ещё раз.");
      },
    });
  };

  return (
    <>
      <Button
        onClick={handleClick}
        loading={joinMutation.isPending}
        disabled={isAlreadyJoined}
        className="w-full"
        variant="primary"
      >
        Вступить
      </Button>

      <InsufficientDepositModal
        open={depositModal !== null}
        onClose={() => setDepositModal(null)}
        requiredKopecks={depositModal?.requiredKopecks ?? 0}
        currentKopecks={depositModal?.currentKopecks ?? 0}
        habitTitle={habit.title}
        onTopUp={() => setTopupOpen(true)}
      />
      <TopUpModal
        open={topupOpen}
        onClose={() => {
          setTopupOpen(false);
          setDepositModal(null);
        }}
        defaultAmount={
          depositModal
            ? Math.max(
                0,
                depositModal.requiredKopecks - depositModal.currentKopecks,
              )
            : undefined
        }
      />
    </>
  );
}
