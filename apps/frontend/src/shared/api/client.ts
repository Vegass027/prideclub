import axios, { type AxiosInstance } from "axios";
import { getInitData } from "@/shared/telegram/tma";

const baseURL = import.meta.env.VITE_API_URL ?? "/api/v1";

export const apiClient: AxiosInstance = axios.create({
  baseURL,
  timeout: 15_000,
});

apiClient.interceptors.request.use((config) => {
  const initData = getInitData();
  if (initData) {
    config.headers["X-Telegram-Init-Data"] = initData;
  }
  return config;
});

apiClient.interceptors.response.use(
  (res) => res,
  (err) => {
    const code = err.response?.data?.code ?? "unknown_error";
    return Promise.reject(new Error(code));
  },
);