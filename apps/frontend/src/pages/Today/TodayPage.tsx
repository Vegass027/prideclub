import { useParams } from "react-router-dom";
import { useToday } from "@/shared/hooks";
import { Button } from "@/shared/ui/Button";
import { StatusDot } from "@/shared/ui/StatusDot";

export function TodayPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const { data, isLoading, isError, error } = useToday(habitId);

  if (isLoading) return <div className="mx-auto max-w-md px-4 py-6 text-sm text-muted">Загрузка...</div>;
  if (isError) return <div className="mx-auto max-w-md px-4 py-6 text-sm text-danger">{String(error)}</div>;
  if (!data) return null;

  const tone =
    data.status === "done"
      ? "success"
      : data.status === "missed"
        ? "danger"
        : "neutral";
  const label =
    data.status === "done"
      ? "Выполнено ✅"
      : data.status === "missed"
        ? "Пропуск ❌"
        : data.status === "pending"
          ? "Окно открыто ⏳"
          : "До старта";

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <h1 className="mb-1 text-2xl font-bold">{data.habit_title}</h1>
      <div className="mb-4 text-xs text-muted">
        окно {data.deadline_at ? "открыто" : "—"} ({data.deposit_balance ? "депозит " + data.deposit_balance : "—"})
      </div>
      <div className="rounded-card bg-surface p-6">
        <div className="mb-4 flex items-center gap-3">
          <StatusDot tone={tone} />
          <span className="text-base font-semibold">{label}</span>
        </div>
        <div className="mb-6">
          <div className="text-5xl font-bold text-gold">{data.streak_days}</div>
          <div className="text-sm text-muted">дней подряд 🔥</div>
        </div>
        <div className="flex flex-col gap-2">
          <Button variant="primary" onClick={() => alert("Перейди в Telegram-бот для чек-ина кружком")}>
            Открыть чат клуба
          </Button>
          <Button variant="secondary" onClick={() => (window.location.href = `/members/${data.habit_id}`)}>
            Участники
          </Button>
          <Button variant="ghost" onClick={() => (window.location.href = `/leaderboard/${data.habit_id}`)}>
            Лидерборд
          </Button>
        </div>
      </div>
    </main>
  );
}