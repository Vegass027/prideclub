import { useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMembers } from "@/shared/hooks";
import { membersApi } from "@/shared/api";
import { MemberCard } from "@/widgets/MemberCard";
import { hapticImpact } from "@/shared/telegram/tma";
import { useState } from "react";

export function MembersPage() {
  const { habitId } = useParams<{ habitId: string }>();
  const { data, isLoading, isError, error } = useMembers(habitId);
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);

  const catchMutation = useMutation({
    mutationFn: (membershipId: string) => membersApi.catch(habitId!, membershipId),
    onSuccess: () => {
      hapticImpact("medium");
      queryClient.invalidateQueries({ queryKey: ["members", habitId] });
    },
  });

  if (isLoading) return <div className="mx-auto max-w-md px-4 py-6 text-sm text-muted">Загрузка...</div>;
  if (isError) return <div className="mx-auto max-w-md px-4 py-6 text-sm text-danger">{String(error)}</div>;
  if (!data) return null;

  return (
    <main className="mx-auto max-w-md px-4 py-6">
      <h1 className="mb-4 text-2xl font-bold">Участники</h1>
      <ul className="flex flex-col gap-2">
        {data.items.map((m) => (
          <li key={m.membership_id}>
            <MemberCard
              member={m}
              busy={busy === m.membership_id}
              onCatch={(id) => {
                setBusy(id);
                catchMutation.mutate(id, { onSettled: () => setBusy(null) });
              }}
            />
          </li>
        ))}
      </ul>
    </main>
  );
}