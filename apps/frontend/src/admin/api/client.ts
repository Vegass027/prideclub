import axios, { type AxiosInstance } from "axios";
import { getInitData } from "@/shared/telegram/tma";

const baseURL = import.meta.env.VITE_ADMIN_API_URL ?? "/admin/v1";

export const adminApi: AxiosInstance = axios.create({
  baseURL,
  timeout: 15_000,
});

adminApi.interceptors.request.use((config) => {
  const initData = getInitData();
  if (initData) {
    config.headers["X-Telegram-Init-Data"] = initData;
  }
  return config;
});

adminApi.interceptors.response.use(
  (res) => res,
  (err) => {
    const code = err.response?.data?.code ?? "unknown_error";
    return Promise.reject(new Error(code));
  },
);
