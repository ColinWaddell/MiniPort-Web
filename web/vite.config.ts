import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.EB200_WEB_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": target,
      "/ws": { target: target.replace(/^http/, "ws"), ws: true },
    },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 600 },
});