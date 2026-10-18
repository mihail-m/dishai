import type { CapacitorConfig } from "@capacitor/cli";
import { loadEnv } from "vite";

// Android Emulator's loopback bridge is enabled only when explicitly selected.
const apiBase = process.env.VITE_API_BASE_URL ?? loadEnv("production", process.cwd()).VITE_API_BASE_URL;
const emulatorApi = apiBase === "http://10.0.2.2:8000";

const config: CapacitorConfig = {
  appId: "app.airpath.demo",
  appName: "AirPath",
  webDir: "dist",
  backgroundColor: "#f3f7ff",
  android: { backgroundColor: "#f3f7ff", allowMixedContent: emulatorApi },
};

export default config;
