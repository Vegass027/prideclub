import { useNavigate, useParams } from "react-router-dom";
import { useMyHabits, useToday } from "@/shared/hooks";
import { formatKopecks } from "@/shared/utils/format";
import { BottomNav } from "@/shared/ui/BottomNav";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { HabitNav } from "@/shared/ui/HabitNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { hapticImpact, openTelegramLink } from "@/shared/telegram/tma";
import { openCheckinTopic, openChatRoot } from "@/shared/telegram/topicLink";
import type { ProofType } from "@/shared/types";

type ProofCfg = { emoji: string; title: string; hint: string };

const PROOF_LABELS: Record<ProofType, ProofCfg> = {
  video_note: {
    emoji: "🎥",
    title: "Видео-кружочек",
    hint: "Отправь видео-кружочек ≥ 3 сек в чат клуба. Бот примет его автоматически.",
  },
  photo: {
    emoji: "📸",
    title: "Фото",
    hint: "Отправь фото в чат клуба. Бот примет его автоматически.",
  },
  text: {
    emoji: "✍️",
    title: "Текст",
    hint: "Отправь текстовое подтверждение в чат клуба.",
  },
};

function resolveProofTypes(habit: { proof_types: ProofType[]; proof_type: ProofType }): ProofType[] {
  if (habit.proof_types.length > 0) return habit.proof_types;
  return [habit.proof_type];
}

export function TodayPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useToday(habitId);
  const { data: myHabits } = useMyHabits();
  const showSwitcher = (myHabits?.items.length ?? 0) > 1;
  const backTo = showSwitcher ? "/profile" : "/profile";

  const handleOpenChat = () => {
    if (!data) return;
    hapticImpact("medium");
    const { habit } = data;
    const url =
      habit.telegram_invite_link ||
      `https://t.me/c/${String(habit.chat_id).replace(/^-100/, "")}`;
    openTelegramLink(url);
  };

  if (isLoading) {
    return (
      <ScreenLayout>
        <PageHeader
          title="Сегодня"
          back
          backTo={backTo}
          right={showSwitcher ? <button onClick={() => navigate("/profile")} className="text-xs text-primary">Сменить клуб</button> : undefined}
        />
        <Skeleton className="h-24 w-full" />
        <div className="mt-4">
          <Skeleton className="h-40 w-full" />
        </div>
        <HabitNav habitId={habitId!} />
      </ScreenLayout>
    );
  }

  if (isError || !data) {
    return (
      <ScreenLayout>
        <PageHeader
          title="Сегодня"
          back
          backTo={backTo}
          right={showSwitcher ? <button onClick={() => navigate("/profile")} className="text-xs text-primary">Сменить клуб</button> : undefined}
        />
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить статус"
          description={String(error ?? "Неизвестная ошибка")}
          action={<Button onClick={() => refetch()}>Повторить</Button>}
        />
        <BottomNav />
      </ScreenLayout>
    );
  }

  const { habit, membership, checkin } = data;
  const allowedProofTypes = resolveProofTypes(habit);
  const singleProof = allowedProofTypes.length === 1;
  const primaryCfg: ProofCfg =
    PROOF_LABELS[allowedProofTypes[0]] ?? PROOF_LABELS.text;

  return (
    <ScreenLayout>
      <PageHeader
        title={habit.title}
        subtitle={habit.timezone}
        back
        backTo={backTo}
        right={
          <div className="flex items-center gap-2">
            <StatusBadge status={checkin.status} />
            {showSwitcher && (
              <button
                onClick={() => navigate("/profile")}
                className="text-xs text-primary"
                aria-label="Сменить клуб"
              >
                Сменить
              </button>
            )}
          </div>
        }
      />

      {habit.description && (
        <section className="mb-3 rounded-card border border-white/5 bg-surface p-3 text-xs text-muted">
          {habit.description}
        </section>
      )}

      {habit.photo_url ? (
        <img
          src={habit.photo_url}
          alt={habit.title}
          className="mb-3 block w-full max-h-56 rounded-card border border-white/5 object-cover"
          loading="lazy"
        />
      ) : null}

      <section className="rounded-card border border-white/5 bg-surface p-4 shadow-card">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-2xl font-bold text-text">
            {checkin.streak_days} <span className="text-base font-normal text-muted">дн. подряд</span>
          </h2>
          <span className="text-xs text-muted">
            Депозит: <strong className="text-text">{formatKopecks(membership.deposit_balance)}</strong>
          </span>
        </div>
        <p className="text-sm text-muted">
          Окно чек-ина: <strong className="text-text">{habit.checkin_window_start}–{habit.checkin_window_end}</strong>
        </p>
      </section>

      <section className="mt-4 rounded-card border-2 border-primary/30 bg-primary/5 p-4">
        <div className="mb-2 flex items-center gap-3">
          <span className="text-3xl" aria-hidden="true">
            {singleProof ? primaryCfg.emoji : "🎯"}
          </span>
          <h3 className="text-base font-semibold text-text">
            {singleProof ? primaryCfg.title : "Чек-ин — любой из типов"}
          </h3>
        </div>
        {singleProof ? (
          <p className="mb-3 text-sm text-muted">{primaryCfg.hint}</p>
        ) : (
          <>
            <p className="mb-3 text-sm text-muted">
              Клуб принимает несколько типов подтверждений. Подходит любой:
            </p>
            <ul className="mb-3 space-y-2">
              {allowedProofTypes.map((t) => {
                const cfg = PROOF_LABELS[t] ?? PROOF_LABELS.text;
                return (
                  <li key={t} className="flex items-start gap-2 text-sm">
                    <span className="text-base" aria-hidden="true">
                      {cfg.emoji}
                    </span>
                    <span>
                      <strong className="text-text">{cfg.title}</strong>
                      <span className="block text-xs text-muted">{cfg.hint}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
        <div className="flex flex-col gap-2">
          {habit.checkin_topic_thread_id !== null ? (
            <Button
              onClick={() => {
                hapticImpact("medium");
                openCheckinTopic(habit.chat_id, habit.checkin_topic_thread_id);
              }}
              className="w-full"
            >
              🎬 Сделать чек-ин
            </Button>
          ) : (
            <Button onClick={handleOpenChat} className="w-full">
              Открыть чат клуба
            </Button>
          )}
          {habit.chat_topic_thread_id !== null && (
            <Button
              variant="secondary"
              onClick={() => {
                hapticImpact("light");
                openCheckinTopic(habit.chat_id, habit.chat_topic_thread_id);
              }}
              className="w-full"
            >
              💬 Перейти в чат
            </Button>
          )}
        </div>
      </section>

      {(habit.telegram_invite_link || habit.chat_id !== 0) && (
        <section className="mt-4 rounded-card border border-white/10 bg-surface p-4">
          {membership.status === "active" ? (
            <>
              <h3 className="mb-2 text-sm font-semibold text-text">
                Клуб в Telegram
              </h3>
              <Button
                disabled
                aria-disabled="true"
                className="w-full"
              >
                ❤️ Вы состоите в клубе
              </Button>
              <p className="mt-2 text-xs text-muted">
                {habit.telegram_invite_link
                  ? "Нажмите на ссылку ниже, чтобы перейти в группу."
                  : "Группа клуба привязана к вашему чату."}
              </p>
              {habit.telegram_invite_link && (
                <button
                  type="button"
                  onClick={() => {
                    hapticImpact("light");
                    openChatRoot(
                      habit.chat_id,
                      habit.telegram_invite_link ?? null,
                    );
                  }}
                  className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-white/10 bg-surface px-4 py-2 text-sm font-medium text-text transition hover:border-white/20"
                >
                  💬 Открыть группу
                </button>
              )}
            </>
          ) : (
            <>
              <h3 className="mb-2 text-sm font-semibold text-text">
                Клуб в Telegram
              </h3>
              <p className="mb-3 text-xs text-muted">
                Чтобы бот принимал твои чек-ины и участники могли тебя
                «поймать», нужно вступить в группу клуба в Telegram.
              </p>
              <Button
                onClick={() => {
                  hapticImpact("medium");
                  openChatRoot(
                    habit.chat_id,
                    habit.telegram_invite_link ?? null,
                  );
                }}
                className="w-full"
              >
                👋 Присоединиться к клубу
              </Button>
            </>
          )}
        </section>
      )}

      <section className="mt-4 rounded-card border border-white/5 bg-surface p-4 text-sm text-muted">
        <h3 className="mb-2 text-sm font-semibold text-text">Что дальше</h3>
        <ul className="list-disc space-y-1 pl-5">
          <li>Бот примет твоё подтверждение автоматически.</li>
          <li>Пропустишь окно — штраф {formatKopecks(habit.penalty_amount)} уйдёт в призовой фонд клуба.</li>
          <li>Участники могут «поймать» тебя, если ты не отметился — те же деньги в фонд.</li>
        </ul>
      </section>

      {checkin.status === "missed" && (
        <section className="mt-4 rounded-card border border-danger/30 bg-danger/10 p-4 text-sm">
          <strong className="block text-danger">Сегодня пропуск.</strong>
          <span className="text-muted">
            Штраф уже списан в призовой фонд клуба.
          </span>
        </section>
      )}

      <HabitNav habitId={habit.id} />
    </ScreenLayout>
  );
}
