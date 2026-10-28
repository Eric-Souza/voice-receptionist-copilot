import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8000 and the dev server proxies to it, so the app uses relative URLs.
// A deployed build points at the API with VITE_API_URL instead.
const api = "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": api,
      "/ws": { target: api, ws: true },
    },
  },
});
