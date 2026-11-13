import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// FastAPI serves the build under /app, so assets resolve relative to it.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: { proxy: { "/v1": "http://localhost:8100" } },
});
