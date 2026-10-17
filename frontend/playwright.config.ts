import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5174", viewport: { width: 1440, height: 1000 },
    colorScheme: "light", screenshot: "only-on-failure", trace: "retain-on-failure",
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    launchOptions: { args: ["--enable-webgl", "--enable-unsafe-swiftshader"] },
  },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174 --strictPort",
    url: "http://127.0.0.1:5174", reuseExistingServer: !process.env.CI,
  },
});
