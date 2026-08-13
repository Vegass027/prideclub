import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter } from "react-router-dom";
import { AdminAppRouter } from "./router";
import { initTelegram } from "@/shared/telegram/tma";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function AdminApp() {
  useEffect(() => {
    initTelegram();
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/admin">
        <AdminAppRouter />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
