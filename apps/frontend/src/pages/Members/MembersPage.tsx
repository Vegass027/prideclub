import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useCatch, useMembers, useMyHabits } from "@/shared/hooks";
import { Avatar } from "@/shared/ui/Avatar";
import { BottomNav } from "@/shared/ui/BottomNav";
import { EmptyState } from "@/shared/ui/EmptyState";
import { HabitNav } from "@/shared/ui/HabitNav";
import { PageHeader } from "@/shared/ui/PageHeader";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { Skeleton } from "@/shared/ui/Skeleton";
import { StatusBadge } from "@/shared/ui/StatusBadge";
import { hapticImpact, hapticNotify } from "@/shared/telegram/tma";
import type { CatchCode, CatchResponse, MemberRow } from "@/shared/types";

const CATCH_ERROR_LABELS: Record<string, string> = {
  catcher_is_violator: "Нельзя поймать себя",
  violator_has_checkin: "Участник уже отметился",
  penalty_already_processed: "Штраф уже начислен",
  deposit_exhausted: "Депозит нарушителя пуст",
  membership_not_active: "Членство не активно",
  habit_not_found: "Клуб не найден",
  rate_limited: "Слишком много попыток — подожди",
};

export function MembersPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useMembers(habitId);
  const catchMutation = useCatch(habitId);
  const { data: myHabits } = useMyHabits();
  const showSwitcher = (myHabits?.items.length ?? 0) > 1;
  const backTo = showSwitcher ? "/profile" : "/profile";
  const [catchMessage, setCatchMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const headerRight = showSwitcher ? (
    <button onClick={() => navigate("/profile")} className="text-xs text-primary">Сменить клуб</button>
  ) : undefined;

  const handleCatch = (m: MemberRow) => {
    if (!m.can_catch) return;
    hapticImpact("heavy");
    catchMutation.mutate(m.membership_id, {
      onSuccess: (res: CatchResponse) => {
        if (res.ok) {
          hapticNotify("success");
          setCatchMessage({ ok: true, text: `+1 поинт. Штраф списан в фонд клуба.` });
        } else {
          hapticNotify("warning");
          setCatchMessage({ ok: false, text: CATCH_ERROR_LABELS[res.code as CatchCode] ?? "Не удалось поймать" });
        }
        refetch();
        setTimeout(() => setCatchMessage(null), 3000);
      },
      onError: () => {
        hapticNotify("error");
        setCatchMessage({ ok: false, text: "Ошибка сети" });
      },
    });
  };

  if (isLoading) {
    return (
      <ScreenLayout>
        <PageHeader title="Участники" back backTo={backTo} right={headerRight} />
        <Skeleton className="h-16 w-full" rows={4} />
        <HabitNav habitId={habitId!} />
      </ScreenLayout>
    );
  }

  if (isError) {
    return (
      <ScreenLayout>
        <PageHeader title="Участники" back backTo={backTo} right={headerRight} />
        <EmptyState icon="⚠️" title="Не удалось загрузить список" description={String(error)} />
        <BottomNav />
      </ScreenLayout>
    );
  }

  const items = data?.items ?? [];
  const violators = items.filter((m) => m.status === "missed" && m.can_catch);
  const others = items.filter((m) => !violators.includes(m));

  return (
    <ScreenLayout>
      <PageHeader title="Участники" subtitle={`${items.length} в клубе`} back backTo={backTo} right={headerRight} />

      {catchMessage && (
        <div
          role="alert"
          className={`mb-3 rounded-card border p-3 text-sm ${
            catchMessage.ok
              ? "border-success/30 bg-success/10 text-success"
              : "border-warning/30 bg-warning/10 text-warning"
          }`}
        >
          {catchMessage.text}
        </div>
      )}

      {violators.length > 0 && (
        <section className="mb-4">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-danger">
            ⚠️ Можно поймать ({violators.length})
          </h2>
          <ul className="space-y-2">
            {violators.map((m) => (
              <li key={m.membership_id}>
                <MemberRowItem
                  row={m}
                  busy={catchMutation.isPending && catchMutation.variables === m.membership_id}
                  onCatch={() => handleCatch(m)}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          Все участники ({others.length})
        </h2>
        {others.length === 0 ? (
          <EmptyState icon="👥" title="Тут пока никого кроме тебя" />
        ) : (
          <ul className="space-y-2">
            {others.map((m) => (
              <li key={m.membership_id}>
                <MemberRowItem row={m} onCatch={() => handleCatch(m)} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <HabitNav habitId={habitId!} />
    </ScreenLayout>
  );
}

interface MemberRowItemProps {
  row: MemberRow;
  busy?: boolean;
  onCatch?: () => void;
}

function MemberRowItem({ row, busy, onCatch }: MemberRowItemProps) {
  return (
    <article className="flex items-center gap-3 rounded-card border border-white/5 bg-surface p-3">
      <Avatar src={null} fallback={row.first_name} size="md" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-text">{row.first_name}</span>
          {row.username && <span className="text-xs text-muted">@{row.username}</span>}
        </div>
        <div className="mt-0.5 flex items-center gap-2">
          <StatusBadge status={row.status} />
          {row.streak_days > 0 && (
            <span className="text-xs text-muted">🔥 {row.streak_days}</span>
          )}
        </div>
      </div>
      {row.can_catch && onCatch && (
        <button
          type="button"
          onClick={onCatch}
          disabled={busy}
          className="rounded-full bg-danger px-3 py-1.5 text-xs font-semibold text-white transition active:scale-95 disabled:opacity-50"
          aria-label={`Поймать ${row.first_name}`}
        >
          {busy ? "..." : "Поймать"}
        </button>
      )}
    </article>
  );
}
