import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev mode: `npm run dev` serves the React app on :5173 and proxies API
// calls to the FastAPI backend on :8000.
// Production: `npm run build` outputs to dist/, which FastAPI serves itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
