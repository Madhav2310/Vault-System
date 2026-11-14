import { expect, test, type Page } from "@playwright/test";

// S19 follow-up: the Metrics page. The API is mocked with page.route so the numbers are known.
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

const documents = {
  scope: "owner", documents: 3, by_status: { enriched: 2, failed: 1 }, by_mime: { "application/pdf": 3 }, needs_ocr: 0,
  pages: 41, chunks: { total: 210 }, storage_bytes: 1544021, time_to_ready_seconds: { p50: 38.2, p95: 71 },
  enriched: 2, by_category: { contract: 2 }, brief_cost_usd: { total: 0.01 }, facts: { total: 18, verified_ratio: 0.94 },
};
const processing = (jobs: number) => ({
  scope: "owner", window: "24h", throughput: { jobs_completed: jobs, stage_runs: 18, jobs_per_hour: 0.125 },
  stage_duration_ms: { extract: { count: 3, p50: 840, p95: 1900 } }, failures_by_error_code: { corrupt: 1 }, retries: 0,
  redispatch_candidates: 0, oldest_pending_age_seconds: null,
});
const chat = {
  scope: "owner", window: "24h", quota: { limit_usd: 5, spent_today_usd: 1.25 }, turns: 12, outcomes: { answer: 11, abstain: 1 },
  coverage: { full: 10, partial: 2 }, latency_ms: { p50: 2100, p95: 5400 }, ttft_ms: { p50: 600, p95: 1500 },
  cost_per_turn_usd: { p50: 0.004, p95: 0.012 }, node_latency_ms: {}, degraded: {},
  feedback: { up: 7, down: 2, by_reason: { wrong: 1, incomplete: 1 } },
  llm: { calls: 40, total_cost_usd: 0.1234, cache_hit_ratio: 0.5, tokens: [], cost_by_purpose: {} },
};

async function mock(page: Page, windows: string[] = []) {
  await page.addInitScript(() => localStorage.setItem("vault.apiKey", "vk_test_demo"));
  await page.route("**/v1/metrics/documents", (r) => r.fulfill(json(documents)));
  await page.route("**/v1/metrics/processing**", (r) => {
    const w = new URL(r.request().url()).searchParams.get("window") ?? "";
    windows.push(w);
    return r.fulfill(json(processing(w === "7d" ? 99 : 3)));
  });
  await page.route("**/v1/metrics/chat**", (r) => r.fulfill(json(chat)));
  await page.route("**/v1/documents?**", (r) => r.fulfill(json({ items: [], next_cursor: null })));
}

test("metrics page shows document, processing and chat figures", async ({ page }) => {
  const windows: string[] = [];
  await mock(page, windows);
  await page.goto("/app/");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Metrics" }).click();
  await expect(page).toHaveURL(/\/app\/metrics$/);
  await expect(page.getByRole("heading", { name: "Metrics", level: 1 })).toBeVisible();

  await expect(page.getByTestId("stat-documents")).toContainText("3");
  await expect(page.getByTestId("metrics-m-docs")).toContainText("210");
  await expect(page.getByTestId("metrics-m-docs")).toContainText("38.2 s / 71.0 s");
  await expect(page.getByTestId("metrics-m-docs")).toContainText("94%");
  await expect(page.getByTestId("stat-jobs")).toContainText("3");
  await expect(page.getByTestId("metrics-m-processing")).toContainText("Corrupt");
  await expect(page.getByTestId("stat-latency")).toContainText("2.1 s / 5.4 s");
  await expect(page.getByTestId("stat-ttft")).toContainText("600 ms / 1.5 s");
  await expect(page.getByTestId("stat-cost")).toContainText("$0.12");
  await expect(page.getByTestId("stat-feedback")).toContainText("7 / 2");
  await expect(page.getByTestId("stat-quota")).toContainText("$1.25 of $5.00 used");

  await page.getByLabel("Time range").selectOption("7d");
  await expect(page.getByTestId("stat-jobs")).toContainText("99");
  expect(windows).toEqual(["24h", "7d"]);
});

test("metrics page reports a failing endpoint without hiding the others", async ({ page }) => {
  await mock(page);
  await page.route("**/v1/metrics/chat**", (r) => r.fulfill(json({ type: "about:blank", title: "Error", status: 500, code: "internal", detail: null, retryable: false }, 500)));
  await page.goto("/app/metrics");
  await expect(page.getByTestId("stat-documents")).toContainText("3");
  await expect(page.getByTestId("metrics-m-chat").getByRole("alert")).toBeVisible();
});
