import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    hmr: {
      protocol: "wss",
    },
    allowedHosts: [".telegram.org", ".t.me"],
  },
  build: {
    target: "es2020",
    sourcemap: true,
  },
});