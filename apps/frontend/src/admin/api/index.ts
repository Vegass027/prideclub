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
  proof_types: AdminHabitProofType[];
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
  checkin_topic_thread_id: number | null;
  notifications_topic_thread_id: number | null;
  chat_topic_thread_id: number | null;
  checkin_topic_link: string | null;
  notifications_topic_link: string | null;
  chat_topic_link: string | null;
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

export interface AdminHabitChatStatusResponse {
  ok: boolean;
  habit_id: string;
  chat_id: number;
  bound: boolean;
  code: string | null;
}

export interface AdminHabitPreviewChatResponse {
  ok: boolean;
  chat_id: number | null;
  title: string | null;
  type: string | null;
  invite_link: string;
  already_used_by_habit_id: string | null;
  code: string | null;
  message: string | null;
}

export interface AdminHabitAvailableChat {
  chat_id: number;
  chat_title: string | null;
  chat_type: string | null;
  invite_link: string | null;
  added_at: number;
  bound_to_habit_id: string | null;
  bound_to_habit_title: string | null;
}

export interface AdminHabitAvailableChatsResponse {
  items: AdminHabitAvailableChat[];
}

export interface AdminHabitRefreshChatResponse {
  ok: boolean;
  chat_id: number;
  chat_title: string | null;
  chat_type: string | null;
  invite_link: string | null;
  code: string | null;
  message: string | null;
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
  proof_types: AdminHabitProofType[];
  price_month: number;
  penalty_amount: number;
  stat_gain_per_checkin: number;
  stat_loss_per_miss: number;
  member_limit: number | null;
  curator_id: number | null;
  checkin_topic_link: string;
  notifications_topic_link: string;
  chat_topic_link?: string | null;
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
  proof_types?: AdminHabitProofType[];
  price_month?: number;
  penalty_amount?: number;
  stat_gain_per_checkin?: number;
  stat_loss_per_miss?: number;
  member_limit?: number | null;
  chat_id?: number | null;
  checkin_topic_link?: string;
  notifications_topic_link?: string;
  chat_topic_link?: string;
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
  delete: async (habitId: string): Promise<AdminHabitActionResponse> => {
    const { data } = await adminApi.delete<AdminHabitActionResponse>(
      `/habits/${habitId}`,
    );
    return data;
  },
  restore: async (habitId: string): Promise<AdminHabitActionResponse> => {
    const { data } = await adminApi.post<AdminHabitActionResponse>(
      `/habits/${habitId}/restore`,
    );
    return data;
  },
  permanentDelete: async (
    habitId: string,
  ): Promise<AdminHabitActionResponse> => {
    const { data } = await adminApi.delete<AdminHabitActionResponse>(
      `/habits/${habitId}/permanent`,
    );
    return data;
  },
  chatStatus: async (habitId: string): Promise<AdminHabitChatStatusResponse> => {
    const { data } = await adminApi.get<AdminHabitChatStatusResponse>(
      `/habits/${habitId}/chat_status`,
    );
    return data;
  },
  previewChatByInvite: async (
    inviteLink: string,
  ): Promise<AdminHabitPreviewChatResponse> => {
    const { data } = await adminApi.post<AdminHabitPreviewChatResponse>(
      "/habits/preview_chat_by_invite",
      { invite_link: inviteLink },
    );
    return data;
  },
  availableChats: async (): Promise<AdminHabitAvailableChatsResponse> => {
    const { data } = await adminApi.get<AdminHabitAvailableChatsResponse>(
      "/habits/available_chats",
    );
    return data;
  },
  refreshChat: async (
    chatId: number,
  ): Promise<AdminHabitRefreshChatResponse> => {
    const { data } = await adminApi.post<AdminHabitRefreshChatResponse>(
      `/habits/refresh_chat/${chatId}`,
      {},
    );
    return data;
  },
  uploadPhoto: async (
    file: File,
  ): Promise<AdminHabitUploadPhotoResponse> => {
    const form = new FormData();
    form.append("file", file);
    const { data } = await adminApi.post<AdminHabitUploadPhotoResponse>(
      "/habits/upload_photo",
      form,
      { headers: { "Content-Type": "multipart/form-data" } },
    );
    return data;
  },
  dismissChat: async (
    chatId: number,
  ): Promise<AdminHabitDismissChatResponse> => {
    const { data } = await adminApi.post<AdminHabitDismissChatResponse>(
      `/habits/dismiss_chat/${chatId}`,
      {},
    );
    return data;
  },
};

export interface AdminHabitUploadPhotoResponse {
  ok: boolean;
  url: string;
  filename: string;
  size: number;
  content_type: string;
}

export interface AdminHabitDismissChatResponse {
  ok: boolean;
  chat_id: number;
  removed_records: number;
}
