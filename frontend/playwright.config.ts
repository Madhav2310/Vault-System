import { defineConfig } from "@playwright/test";

// Run via `make ui-e2e` against the fake-LLM stack: one worker, headless Chromium.
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: process.env.UI_BASE_URL ?? "http://localhost:8100",
    headless: true,
    // `make demo-record` sets DEMO_RECORD=1: video goes to test-results/.
    video: process.env["DEMO_RECORD"] ? { mode: "on", size: { width: 1280, height: 720 } } : "off",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
