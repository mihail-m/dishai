import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// publicDir is the repo-level ./public so districts.geojson is committed once and served as /data/*.
// The repo-level .env is read from ../; ONLY MAP_PROVIDER and the browser key are exposed to the client.
// (GOOGLE_ROUTES_SERVER_KEY must never match this prefix list.)
export default defineConfig({
  plugins: [react(), tailwindcss()],
  publicDir: "../public",
  envDir: "..",
  envPrefix: ["VITE_", "MAP_PROVIDER", "GOOGLE_MAPS_BROWSER_KEY"],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
  build: { chunkSizeWarningLimit: 2500 },
});
