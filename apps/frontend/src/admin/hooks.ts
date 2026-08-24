import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  adminHabitsApi,
  adminStatDefinitionsApi,
  type AdminHabit,
  type AdminHabitCreatePayload,
  type AdminHabitUpdatePayload,
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

export function useDeleteHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (habitId: string) => adminHabitsApi.delete(habitId),
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

export function usePermanentDeleteHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (habitId: string) => adminHabitsApi.permanentDelete(habitId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits"] });
      qc.invalidateQueries({ queryKey: ["admin", "habits", "available_chats"] });
    },
  });
}

export function useChatStatus() {
  return useMutation({
    mutationFn: (habitId: string) => adminHabitsApi.chatStatus(habitId),
  });
}

export function usePreviewChat() {
  return useMutation({
    mutationFn: (inviteLink: string) =>
      adminHabitsApi.previewChatByInvite(inviteLink),
  });
}

export function useAvailableChats() {
  return useQuery({
    queryKey: ["admin", "habits", "available_chats"],
    queryFn: () => adminHabitsApi.availableChats(),
    staleTime: 5_000,
  });
}

export function useRefreshChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chatId: number) => adminHabitsApi.refreshChat(chatId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits", "available_chats"] });
    },
  });
}

export function useUploadPhoto() {
  return useMutation({
    mutationFn: (file: File) => adminHabitsApi.uploadPhoto(file),
  });
}

export function useDismissChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chatId: number) => adminHabitsApi.dismissChat(chatId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "habits", "available_chats"] });
    },
  });
}

export type { AdminHabit };
export type { AdminHabitCreatePayload };


// Phase 3 v2 Task 3.8: каталог stat_definitions для AdminHabitForm.
export function useStatDefinitions() {
  return useQuery({
    queryKey: ["admin", "stat-definitions"],
    queryFn: () => adminStatDefinitionsApi.list(),
    // Каталог 8 canonical меняется редко (только ручное обновление в БД).
    // Кеш 60s + retry 2 для transient network errors.
    staleTime: 60_000,
    retry: 2,
  });
}


// Phase 3 v2 Task 3.8: useUpdateHabit для HabitEditForm.
export function useUpdateHabit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      habitId,
      payload,
    }: {
      habitId: string;
      payload: AdminHabitUpdatePayload;
    }) => adminHabitsApi.update(habitId, payload),
    onSuccess: (_, variables) => {
      // Invalidate list + detail чтобы UI перезагрузил новое значение.
      qc.invalidateQueries({ queryKey: ["admin", "habits"] });
      qc.invalidateQueries({
        queryKey: ["admin", "habits", variables.habitId],
      });
    },
  });
}
