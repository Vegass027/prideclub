import { useState } from "react";
import { useParams } from "react-router-dom";
import { useLeaderboard } from "@/shared/hooks";

const TABS = ["streak", "catches", "shame"] as const;
const LABELS = { streak: "Стрики", catches: "Охотники", shame: "Доска позора" } as const;

export function LeaderboardPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const [tab, setTab] = useState<(typeof TABS)[number]>("streak");
  const { data, isLoading } = useLeaderboard(habitId, tab);

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <h1 className="mb-4 text-2xl font-bold">Лидерборд</h1>
      <div role="tablist" className="mb-4 flex gap-2">
        {TABS.map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-full px-4 py-2 text-sm font-semibold transition ${
              tab === t ? "bg-primary text-white" : "bg-surface text-muted"
            }`}
          >
            {LABELS[t]}
          </button>
        ))}
      </div>
      {isLoading && <div className="text-sm text-muted">Загрузка...</div>}
      {data && (
        <ol className="flex flex-col gap-2">
          {data.items.map((row) => (
            <li key={row.membership_id} className="flex items-center justify-between rounded-card bg-surface p-3">
              <div className="flex items-center gap-3">
                <span className="w-6 text-center text-lg font-bold text-muted">#{row.rank}</span>
                <span className="text-sm">{row.first_name}</span>
              </div>
              <span className="text-base font-semibold text-gold">{row.metric_value}</span>
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}