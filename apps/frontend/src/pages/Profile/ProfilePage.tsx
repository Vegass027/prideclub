import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useBalance, useMyHabits } from "@/shared/hooks";
import { formatDateTime, formatKopecks, transactionTypeLabel } from "@/shared/utils/format";
import { Avatar } from "@/shared/ui/Avatar";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { TopUpModal } from "@/shared/ui/TopUpModal";
import { getUser, getUserPhoto } from "@/shared/telegram/tma";
import type { Transaction } from "@/shared/types";

export function ProfilePage() {
  const tgUser = getUser();
  const photoUrl = getUserPhoto();
  const navigate = useNavigate();
  const { data: balance, isLoading: balanceLoading } = useBalance();
  const { data: myHabits, isLoading: myHabitsLoading } = useMyHabits();
  const [topUpOpen, setTopUpOpen] = useState(false);

  return (
    <ScreenLayout>
      <PageHeader title="Профиль" />

      {tgUser ? (
        <section className="rounded-card border border-white/5 bg-surface p-4 shadow-card">
          <div className="flex items-center gap-3">
            <Avatar
              src={photoUrl}
              fallback={tgUser.first_name ?? "?"}
              size="lg"
              glow
            />
            <div className="min-w-0">
              <p className="truncate text-base font-semibold text-text">
                {tgUser.first_name} {tgUser.last_name}
              </p>
              {tgUser.username && (
                <p className="truncate text-sm text-muted">@{tgUser.username}</p>
              )}
            </div>
          </div>
        </section>
      ) : (
        <Skeleton className="h-20 w-full" />
      )}

      <section className="mt-4 rounded-card border border-white/5 bg-surface p-4 shadow-card">
        <ul className="mb-3 space-y-1 text-xs text-muted">
          <li className="flex gap-2"><span aria-hidden="true">•</span><span>Депозит покрывает штрафы в клубах</span></li>
          <li className="flex gap-2"><span aria-hidden="true">•</span><span>Если депозит пуст — ты выбываешь из клуба <span aria-hidden="true">☹️</span></span></li>
        </ul>
        <div className="flex items-start justify-between border-t border-white/5 pt-3">
          <div>
            <p className="mb-1 text-xs uppercase tracking-wide text-muted">Сейчас на депозите</p>
            {balanceLoading ? (
              <Skeleton className="h-8 w-32" />
            ) : (
              <p className="text-2xl font-bold text-text">{formatKopecks(balance?.deposit_balance ?? 0)}</p>
            )}
          </div>
          <Button
            onClick={() => setTopUpOpen(true)}
            variant="primary"
            className="px-4 py-2 text-sm"
            disabled={(myHabits?.items.length ?? 0) === 0}
            title={
              (myHabits?.items.length ?? 0) === 0
                ? "Нужно состоять хотя бы в одном клубе"
                : undefined
            }
          >
            + Пополнить
          </Button>
        </div>
      </section>

      <section className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
            Мои клубы
          </h2>
          <Button onClick={() => navigate("/marketplace")} variant="secondary" className="min-h-0 px-3 py-1.5 text-xs">
            Все клубы →
          </Button>
        </div>

        {myHabitsLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : (myHabits?.items.length ?? 0) === 0 ? (
          <div className="rounded-card border border-white/5 bg-surface p-4 text-center">
            <p className="mb-3 text-sm text-muted">Ты ещё не вступил ни в один клуб</p>
            <Button onClick={() => navigate("/marketplace")} variant="secondary" className="px-4 py-2 text-sm">
              Выбрать клуб
            </Button>
          </div>
        ) : (
          <ul className="space-y-2">
            {myHabits!.items.map((h) => (
              <li key={h.id}>
                <div className="overflow-hidden rounded-card border border-white/5 bg-surface shadow-card">
                  {h.photo_url ? (
                    <div className="flex w-full items-center justify-center bg-canvas/60">
                      <img
                        src={h.photo_url}
                        alt={h.title}
                        className="block max-h-48 w-full object-contain"
                        loading="lazy"
                      />
                    </div>
                  ) : (
                    <div
                      className="flex h-20 w-full items-center justify-center bg-gradient-to-br from-primary/30 to-primary/5 text-2xl"
                      aria-hidden="true"
                    >
                      🎯
                    </div>
                  )}
                  <div className="p-3">
                    <div className="mb-2 flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-text">〖{h.title}〗</h3>
                        {h.description && (
                          <p className="mt-0.5 line-clamp-1 text-xs text-muted">{h.description}</p>
                        )}
                      </div>
                    </div>
                    <Button
                      onClick={() => navigate(`/habits/${h.id}/today`)}
                      variant="secondary"
                      className="min-h-0 w-full px-3 py-1.5 text-xs"
                    >
                      Открыть клуб →
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {balance && balance.history.length > 0 && (
        <section className="mt-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            История транзакций
          </h2>
          <ul className="space-y-1.5">
            {balance.history.map((tx: Transaction) => (
              <li key={tx.id}>
                <article className="flex items-center gap-3 rounded-card bg-surface/60 px-3 py-2 text-sm">
                  <span className="flex-1 truncate text-muted">{transactionTypeLabel(tx.type)}</span>
                  <span className={`font-bold tabular-nums ${tx.amount >= 0 ? "text-success" : "text-danger"}`}>
                    {tx.amount >= 0 ? "+" : ""}{formatKopecks(tx.amount)}
                  </span>
                  <span className="text-[10px] text-muted">{formatDateTime(tx.created_at)}</span>
                </article>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-4 rounded-card border border-white/5 bg-surface p-4">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          О приложении
        </h2>
        <p className="text-xs leading-relaxed text-muted">
          Habit Club — закрытые клубы дисциплины с денежными штрафами.
          Подтверждай привычку каждый день в чате клуба — деньги остаются в призовом фонде.
          Пропустил — участники «ловят» тебя, штраф уходит в фонд клуба.
        </p>
        <p className="mt-3 text-[10px] text-muted">v0.1.0 · soft-launch</p>
      </section>

      <TopUpModal
        open={topUpOpen}
        onClose={() => setTopUpOpen(false)}
        habits={myHabits?.items ?? []}
      />

      <BottomNav />
    </ScreenLayout>
  );
}
