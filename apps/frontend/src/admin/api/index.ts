import { adminApi } from "./client";

export type AdminHabitProofType = "video_note" | "photo" | "text";

export interface AdminHabit {
  id: string;
  title: string;
  description: string | null;
  chat_id: number;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  penalty_amount: number;
  price_month: number;
  proof_type: AdminHabitProofType;
  prize_pool: number;
  is_active: boolean;
  photo_url: string | null;
  telegram_invite_link: string | null;
  stat_name: string;
  stat_icon: string | null;
  stat_gain_per_checkin: number;
  stat_loss_per_miss: number;
  member_limit: number | null;
  curator_id: number | null;
  archived_at: string | null;
  created_at: string;
  active_members_count: number;
}

export interface AdminHabitsListResponse {
  items: AdminHabit[];
}

export interface AdminHabitActionResponse {
  ok: boolean;
  habit_id: string;
  is_active: boolean;
  archived_at: string | null;
}

export interface AdminHabitCreatePayload {
  title: string;
  description: string | null;
  photo_url: string | null;
  telegram_invite_link: string | null;
  stat_name: string;
  stat_icon: string | null;
  chat_id: number;
  checkin_window_start: string;
  checkin_window_end: string;
  timezone: string;
  proof_type: AdminHabitProofType;
  price_month: number;
  penalty_amount: number;
  stat_gain_per_checkin: number;
  stat_loss_per_miss: number;
  member_limit: number | null;
  curator_id: number | null;
}

export interface AdminHabitUpdatePayload {
  title?: string;
  description?: string | null;
  photo_url?: string | null;
  telegram_invite_link?: string | null;
  stat_name?: string;
  stat_icon?: string | null;
  checkin_window_start?: string;
  checkin_window_end?: string;
  timezone?: string;
  proof_type?: AdminHabitProofType;
  stat_gain_per_checkin?: number;
  stat_loss_per_miss?: number;
  member_limit?: number | null;
}

export const adminHabitsApi = {
  list: async (): Promise<AdminHabitsListResponse> => {
    const { data } = await adminApi.get<AdminHabitsListResponse>("/habits");
    return data;
  },
  get: async (habitId: string): Promise<AdminHabit> => {
    const { data } = await adminApi.get<AdminHabit>(`/habits/${habitId}`);
    return data;
  },
  create: async (payload: AdminHabitCreatePayload): Promise<AdminHabit> => {
    const { data } = await adminApi.post<AdminHabit>("/habits", payload);
    return data;
  },
  update: async (
    habitId: string,
    payload: AdminHabitUpdatePayload,
  ): Promise<AdminHabit> => {
    const { data } = await adminApi.patch<AdminHabit>(
      `/habits/${habitId}`,
      payload,
    );
    return data;
  },
  activate: async (habitId: string, isActive: boolean): Promise<AdminHabitActionResponse> => {
    const { data } = await adminApi.post<AdminHabitActionResponse>(
      `/habits/${habitId}/activate`,
      { is_active: isActive },
    );
    return data;
  },
  archive: async (habitId: string): Promise<AdminHabitActionResponse> => {
    const { data } = await adminApi.post<AdminHabitActionResponse>(
      `/habits/${habitId}/archive`,
    );
    return data;
  },
  restore: async (habitId: string): Promise<AdminHabitActionResponse> => {
    const { data } = await adminApi.post<AdminHabitActionResponse>(
      `/habits/${habitId}/restore`,
    );
    return data;
  },
};
