import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { ScreenLayout } from "@/shared/ui/ScreenLayout";
import { PageHeader } from "@/shared/ui/PageHeader";
import { adminHabitsApi } from "../api";
import { HabitEditForm } from "./HabitEditForm";

export function HabitEditPage() {
  const params = useParams<{ habitId: string }>();
  const habitId = params.habitId ?? "";

  const query = useQuery({
    queryKey: ["admin", "habit", habitId],
    queryFn: () => adminHabitsApi.get(habitId),
    enabled: Boolean(habitId),
  });

  return (
    <ScreenLayout>
      <PageHeader title="Изменить клуб" back backTo="/habits" />
      <HabitEditForm
        habit={query.data ?? null}
        loading={query.isLoading}
        error={query.isError ? String(query.error) : null}
      />
    </ScreenLayout>
  );
}
