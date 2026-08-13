import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWallet } from "@/shared/hooks/useWallet";
import { Button } from "@/shared/ui/Button";
import { JoinPayModal, type JoinPayModalMode } from "@/shared/ui/JoinPayModal";
import { hapticImpact } from "@/shared/telegram/tma";
import type { Habit } from "@/shared/types";

interface JoinButtonProps {
  habit: Habit;
  /**
   * Pravki-deposit-sse.md §Z-3.3: если уже известно, что юзер
   * состоит в клубе, кнопку можно отключить. Используется MarketplacePage
   * для случая когда myHabits уже содержит этот клуб (status=ACTIVE).
   *
   * Pravki-subscribe-and-join.md §Z-17: даже если isAlreadyJoined=false,
   * мы ВСЁ РАВНО проверяем `useWallet().active_clubs` для определения
   * режима модалки (full vs deposit-only, см. §Z-13.1 матрица).
   */
  isAlreadyJoined?: boolean;
}

/**
 * Pravki-subscribe-and-join.md §Z-17 (substep 2): кнопка «Вступить» с интеграцией JoinPayModal.
 *
 * Извлечён из MarketplacePage (Pravki-deposit-sse.md §Z-3.5) для тестируемости.
 *
 * Поведение (Pravki-subscribe-and-join.md §Z-17):
 * 1. Pre-check из useWallet().active_clubs: если для этого habit'а есть
 *    ACTIVE/PAUSED membership с subscription_until >= today → mode="deposit-only"
 *    (списываем только депозит, подписка уже оплачена). Иначе mode="full"
 *    (чекбокс подписки + депозит, см. §Z-13.1 матрица).
 * 2. Loading-состояние: пока useWallet().isLoading === true, кнопка показывает
 *    спиннер (Button.loading prop) и onClick — early-return. Модалка НЕ
 *    открывается без данных о подписке — иначе риск двойного списания.
 * 3. Click → JoinPayModal с выбранным mode. На success → navigate(/habits/{id}/today)
 *    БЕЗ window.location.reload() (требование PR #2).
 *
 * Известный gap (см. §Z-17 substep 2 отчёт): LEFT membership с активной
 * подпиской фильтруется из active_clubs (по `status != LEFT` в users.py:124).
 * Pre-check не видит её → mode="full" по умолчанию → пользователю предложат
 * заплатить подписку второй раз. Для MVP приемлемо (редкий кейс), бэкенд
 * defensive-валидирует (заряжается только если subscription_until < today,
 * см. §Z-13.1 матрица). На будущее — расширить wallet endpoint чтобы
 * включал LEFT membership'ы с активной подпиской.
 */
export function JoinButton({ habit, isAlreadyJoined = false }: JoinButtonProps) {
  const navigate = useNavigate();
  const { data: wallet, isLoading: isWalletLoading } = useWallet();
  const [payModalOpen, setPayModalOpen] = useState(false);

  // Pravki-subscribe-and-join.md §Z-13.1 матрица: определяем режим модалки.
  // subscription_until сравниваем строкой "YYYY-MM-DD" — ISO date сортируется
  // лексикографически так же как хронологически, без необходимости в Date().
  const walletClub = wallet?.active_clubs.find((c) => c.habit_id === habit.id);
  const today = new Date().toISOString().slice(0, 10);
  const hasActiveSubscription = !!(
    walletClub?.subscription_until && walletClub.subscription_until >= today
  );
  // Если клуб найден в active_clubs (ACTIVE/PAUSED) И подписка активна —
  // режим deposit-only. Во всех остальных случаях (нет membership, или
  // LEFT отфильтрован, или подписка истекла) — full (новая оплата подписки).
  const mode: JoinPayModalMode =
    walletClub && hasActiveSubscription ? "deposit-only" : "full";

  const handleClick = () => {
    // Защита от ложного срабатывания: пока useWallet не вернул данные,
    // не открываем модалку. Иначе mode будет "full" по умолчанию даже
    // для юзера с активной подпиской → двойное списание.
    if (isWalletLoading || wallet === undefined) return;
    hapticImpact("medium");
    setPayModalOpen(true);
  };

  const handleSuccess = () => {
    // onSuccess из JoinPayModal (после успешной оплаты) → navigate.
    // useJoinAndPay внутри JoinPayModal уже инвалидирует все нужные queries.
    navigate(`/habits/${habit.id}/today`);
  };

  return (
    <>
      <Button
        onClick={handleClick}
        // loading prop показывает спиннер и блокирует клик. Текст «Вступить»
        // не меняется — это UX-паттерн «загрузка данных перед действием».
        loading={isWalletLoading}
        // Дополнительная защита: если данные ещё не пришли, кнопка disabled.
        disabled={isAlreadyJoined || isWalletLoading || wallet === undefined}
        className="w-full"
        variant="primary"
      >
        Вступить
      </Button>

      <JoinPayModal
        open={payModalOpen}
        onClose={() => setPayModalOpen(false)}
        onSuccess={handleSuccess}
        habit={{
          id: habit.id,
          title: habit.title,
          penalty_amount: habit.penalty_amount,
          price_month: habit.price_month,
        }}
        mode={mode}
      />
    </>
  );
}
