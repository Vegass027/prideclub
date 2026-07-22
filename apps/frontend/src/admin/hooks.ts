import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  adminHabitsApi,
  type AdminHabit,
  type AdminHabitCreatePayload,
} from "./api";

export function useAdminHabits() {
  return useQuery({
    queryKey: ["admin", "habits"],
    queryFn: () => adminHabitsApi.list(),
    staleTime: 15_000,
  });
}

export function useCreateHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: AdminHabitCreatePayload) => adminHabitsApi.create(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits"] });
    },
  });
}

export function useActivateHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ habitId, isActive }: { habitId: string; isActive: boolean }) =>
      adminHabitsApi.activate(habitId, isActive),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits"] });
    },
  });
}

export function useArchiveHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (habitId: string) => adminHabitsApi.archive(habitId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits"] });
    },
  });
}

export function useRestoreHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (habitId: string) => adminHabitsApi.restore(habitId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits"] });
    },
  });
}

export type { AdminHabit };
export type { AdminHabitCreatePayload };
