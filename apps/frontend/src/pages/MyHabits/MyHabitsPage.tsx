import { useNavigate } from "react-router-dom";
import { useMyHabits } from "@/shared/hooks";
import { openCheckinTopic } from "@/shared/telegram/topicLink";
import { hapticImpact } from "@/shared/telegram/tma";
import { BottomNav } from "@/shared/ui/BottomNav";
import { EmptyState } from "@/shared/ui/EmptyState";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";

export function MyHabitsPage() {
  const { data, isLoading } = useMyHabits();
  const navigate = useNavigate();
  const items = data?.items ?? [];

  return (
    <ScreenLayout>
      <PageHeader title="Мои клубы" />

      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : items.length === 0 ? (
        <EmptyState
          icon="🏪"
          title="Ты ещё не в клубах"
          description="Вступи в любой клуб — деньги пойдут в призовой фонд."
          action={
            <button
              onClick={() => navigate("/marketplace")}
              className="mt-4 inline-flex items-center justify-center rounded-md bg-primary px-5 py-2.5 text-sm font-semibold text-canvas transition hover:bg-primary/90"
            >
              Выбрать клуб
            </button>
          }
        />
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((h) => {
            const canCheckIn =
              h.chat_id !== 0 && h.checkin_topic_thread_id !== null;
            const canOpenChat =
              h.chat_id !== 0 && h.chat_topic_thread_id !== null;
            const canJoinGroup = Boolean(h.telegram_invite_link);
            const handleJoinGroup = () => {
              hapticImpact("medium");
              const tg = window.Telegram?.WebApp;
              if (tg?.openTelegramLink && h.telegram_invite_link) {
                tg.openTelegramLink(h.telegram_invite_link);
              } else if (tg?.openLink && h.telegram_invite_link) {
                tg.openLink(h.telegram_invite_link);
              } else if (h.telegram_invite_link) {
                window.open(h.telegram_invite_link, "_blank", "noopener,noreferrer");
              }
            };
            return (
              <li key={h.id}>
                <div className="rounded-card border border-white/5 bg-surface p-4 shadow-card">
                  <button
                    type="button"
                    onClick={() => navigate(`/habits/${h.id}/today`)}
                    className="block w-full text-left"
                  >
                    <h2 className="text-base font-semibold text-text">{h.title}</h2>
                    {h.description && (
                      <p className="mt-1 line-clamp-3 text-xs text-muted">
                        {h.description}
                      </p>
                    )}
                    <div className="mt-2 flex items-center justify-between text-xs">
                      <span className="text-muted">
                        Окно: {h.checkin_window_start}–{h.checkin_window_end}
                      </span>
                      <span className="text-primary">Открыть →</span>
                    </div>
                  </button>
                  <div className="mt-3 flex flex-col gap-2">
                    {canJoinGroup && (
                      <button
                        type="button"
                        onClick={handleJoinGroup}
                        className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/10 bg-surface px-4 py-2.5 text-sm font-medium text-text transition hover:border-white/20"
                      >
                        <span aria-hidden="true">👋</span>
                        Присоединиться к клубу
                      </button>
                    )}
                    {canCheckIn && (
                      <button
                        type="button"
                        onClick={() =>
                          openCheckinTopic(h.chat_id, h.checkin_topic_thread_id)
                        }
                        className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-canvas transition hover:bg-primary/90"
                      >
                        <span aria-hidden="true">🎬</span>
                        Сделать чек-ин
                      </button>
                    )}
                    {canOpenChat && (
                      <button
                        type="button"
                        onClick={() =>
                          openCheckinTopic(h.chat_id, h.chat_topic_thread_id)
                        }
                        className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/10 bg-surface px-4 py-2.5 text-sm font-medium text-text transition hover:border-white/20"
                      >
                        <span aria-hidden="true">💬</span>
                        Перейти в чат
                      </button>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <BottomNav />
    </ScreenLayout>
  );
}
