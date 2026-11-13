import { defineConfig } from "@playwright/test";

// Run via `make ui-e2e` (S19.02) against the fake-LLM stack: one worker, headless Chromium.
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  fullyParallel: false,
  use: { baseURL: process.env.UI_BASE_URL ?? "http://localhost:8100", headless: true },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
