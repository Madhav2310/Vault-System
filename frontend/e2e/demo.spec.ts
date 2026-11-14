import { expect, test, type Page } from "@playwright/test";

// Scripted walk-through for the demo recording: `make demo-record` (needs `make up`; DEMO_RECORD=1 turns
// video on). Skipped in `make ui-e2e`. It signs in through local autologin, so it only runs against localhost.
// The questions are the ones the fake model scripts (backend/tests/fixtures/ui_script.yaml), so it also
// runs on the fake-model stack. Slow, deliberate pacing is intended: this is a recording, not a test.
test.skip(!process.env["DEMO_RECORD"], "set DEMO_RECORD=1 (make demo-record) to run the scripted demo");
test.setTimeout(240_000);

const FIXTURES = ["e2e/fixtures/services-agreement.txt", "e2e/fixtures/pricing-memo.txt"];
const beat = (page: Page, ms = 1500) => page.waitForTimeout(ms);

async function ask(page: Page, question: string) {
  await page.getByLabel("Your question").fill(question);
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("status-line")).toHaveCount(0, { timeout: 90_000 });
}

test("demo walk-through", async ({ page }) => {
  // 1. Upload and live processing status
  await page.goto("/app/");
  await beat(page);
  await page.getByLabel("Upload documents").setInputFiles(FIXTURES);
  await expect(page.locator('[data-testid="doc-row"][data-status="enriched"]').nth(1)).toBeVisible({ timeout: 120_000 });
  await beat(page);

  // 2. Insights: the source guide, then a fact opens the cited sentence
  await page.getByTestId("doc-row").filter({ hasText: /services/i }).getByRole("link").first().click();
  await expect(page.getByTestId("fact").first()).toBeVisible({ timeout: 30_000 });
  await beat(page, 2500);
  await page.getByRole("button", { name: /Citation 1/ }).first().click();
  await expect(page.getByTestId("source-viewer")).toBeVisible();
  await expect(page.getByTestId("cited-sentence").first()).toBeVisible();
  await beat(page, 3000);

  // 3. Cited chat, then click a citation through to the stored sentence
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Chat" }).click();
  await ask(page, "What is the notice period for termination?");
  const marker = page.getByTestId("citation-marker").first();
  await marker.hover();
  await expect(page.getByTestId("citation-card")).toBeVisible();
  await beat(page, 2000);
  await marker.click();
  await expect(page.getByTestId("source-viewer")).toBeVisible();
  await expect(page.getByTestId("cited-sentence").first()).toBeVisible();
  await beat(page, 3000);

  // 4. Cross-document comparison with a conflict callout
  await ask(page, "What is the monthly fee?");
  await expect(page.getByTestId("conflict").first()).toBeVisible({ timeout: 30_000 });
  await beat(page, 3500);

  // 5. A question the documents cannot answer: abstention
  await ask(page, "What is the CEO's favourite colour?");
  await expect(page.getByTestId("abstain-card")).toBeVisible();
  await beat(page, 3000);

  // 6. Metrics page
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Metrics" }).click();
  await expect(page.getByRole("heading", { name: "Metrics", level: 1 })).toBeVisible();
  await expect(page.getByTestId("stat-documents")).toBeVisible();
  await beat(page, 3500);

  // 7. The metrics API as JSON in the browser (the browser session carries the local sign-in)
  await page.goto("/v1/metrics/documents");
  await beat(page, 3000);
});
