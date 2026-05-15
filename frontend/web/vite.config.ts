import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxy target for /api/* requests. Default points to a locally-running
// backend on the host; Docker Compose overrides this to the inter-container
// service hostname via the BACKEND_URL env var.
const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: BACKEND_URL,
        changeOrigin: true,
      },
    },
  },
});
