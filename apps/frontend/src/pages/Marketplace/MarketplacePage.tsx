import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useMarketplace } from "@/shared/hooks";
import type { Habit } from "@/shared/types";
import { HabitCard } from "@/widgets/HabitCard";
import { apiClient } from "@/shared/api/client";

async function joinHabit(habitId: string): Promise<void> {
  await apiClient.post(`/habits/${habitId}/join`);
}

export function MarketplacePage() {
  const { data, isLoading, isError, error } = useMarketplace();
  const queryClient = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);

  const joinMutation = useMutation({
    mutationFn: joinHabit,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["marketplace"] }),
  });

  if (isLoading) return <MarketplaceSkeleton />;
  if (isError) return <ErrorMessage message={String(error)} />;

  const items: Habit[] = data?.items ?? [];

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <header className="mb-4">
        <h1 className="text-2xl font-bold">Маркетплейс привычек</h1>
        <p className="text-sm text-muted">Выберите клуб — дисциплина начинается сейчас.</p>
      </header>
      <ul className="flex flex-col gap-3">
        {items.map((h) => (
          <li key={h.id}>
            <HabitCard
              habit={h}
              joined={false}
              busy={busyId === h.id}
              onJoin={(id) => {
                setBusyId(id);
                joinMutation.mutate(id, { onSettled: () => setBusyId(null) });
              }}
              onOpen={(id) => (window.location.href = `/today/${id}`)}
            />
          </li>
        ))}
      </ul>
      {items.length === 0 && (
        <p className="mt-8 text-center text-sm text-muted">Клубы скоро появятся — приходите позже.</p>
      )}
    </main>
  );
}

function MarketplaceSkeleton() {
  return (
    <div className="mx-auto max-w-md space-y-3 px-4 py-6">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-24 animate-pulse rounded-card bg-surface" />
      ))}
    </div>
  );
}

function ErrorMessage({ message }: { message: string }) {
  return (
    <div role="alert" className="mx-auto max-w-md px-4 py-6 text-sm text-danger">
      Ошибка загрузки: {message}
    </div>
  );
}