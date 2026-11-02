import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the page runs on :5173 and proxies API and websocket traffic to the FastAPI
// server on :8000. In production FastAPI serves the built files itself, so there is one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
});
