import { useParams } from "react-router-dom";
import { useToday } from "@/shared/hooks";
import { formatKopecks } from "@/shared/utils/format";
import { Button } from "@/shared/ui/Button";
import { EmptyState } from "@/shared/ui/EmptyState";
import { NavOutlet } from "@/shared/ui/BottomNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Skeleton } from "@/shared/ui/Skeleton";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { hapticImpact } from "@/shared/telegram/tma";

const PROOF_LABELS: Record<string, { emoji: string; title: string; hint: string }> = {
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

export function TodayPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const { data, isLoading, isError, error, refetch } = useToday(habitId);

  const handleOpenChat = () => {
    if (!data) return;
    hapticImpact("medium");
    window.open(`https://t.me/c/${String(data.habit.chat_id).replace(/^-100/, "")}`, "_blank");
  };

  if (isLoading) {
    return (
      <main className="mx-auto max-w-md px-4 py-6">
        <PageHeader title="Сегодня" back />
        <Skeleton className="h-24 w-full" />
        <div className="mt-4">
          <Skeleton className="h-40 w-full" />
        </div>
      </main>
    );
  }

  if (isError || !data) {
    return (
      <main className="mx-auto max-w-md px-4 py-6">
        <PageHeader title="Сегодня" back />
        <EmptyState
          icon="⚠️"
          title="Не удалось загрузить статус"
          description={String(error ?? "Неизвестная ошибка")}
          action={<Button onClick={() => refetch()}>Повторить</Button>}
        />
      </main>
    );
  }

  const { habit, membership, checkin } = data;
  const proofCfg = PROOF_LABELS[habit.proof_type] ?? PROOF_LABELS.text;

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <PageHeader
        title={habit.title}
        subtitle={habit.timezone}
        back
        right={<StatusBadge status={checkin.status} />}
      />

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
            {proofCfg.emoji}
          </span>
          <h3 className="text-base font-semibold text-text">{proofCfg.title}</h3>
        </div>
        <p className="mb-3 text-sm text-muted">{proofCfg.hint}</p>
        <Button onClick={handleOpenChat} className="w-full">
          Открыть чат клуба
        </Button>
      </section>

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

      <NavOutlet>
        <div />
      </NavOutlet>
    </main>
  );
}
