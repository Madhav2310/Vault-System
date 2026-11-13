import { expect, test, type Page } from "@playwright/test";

// Written for S19.03; run by `make ui-e2e` (S19.02) against the fake-LLM stack. Not run in the S19.01/03 lane.
const API_KEY = process.env.UI_API_KEY ?? "vk_test_demo";

async function signIn(page: Page) {
  await page.addInitScript((k) => localStorage.setItem("vault.apiKey", k), API_KEY);
  await page.goto("/app/");
}

const textFile = (name: string, body = "Quarterly revenue grew 12 percent. Margins were stable.\n") => ({
  name,
  mimeType: "text/plain",
  buffer: Buffer.from(body),
});

test("upload shows live stages", async ({ page }) => {
  await signIn(page);
  await page.getByLabel("Upload documents").setInputFiles(textFile(`live-${Date.now()}.txt`, `live ${Date.now()}\n`));
  const stages = page.getByRole("list", { name: /^Processing / });
  await expect(stages.getByText("Reading")).toBeVisible();
  await expect(page.locator('[data-testid="doc-row"][data-status="enriched"]').first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("Ready").first()).toBeVisible();
});

test("rejects oversized", async ({ page }) => {
  await signIn(page);
  let uploads = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/v1/documents")) uploads++;
  });
  await page.getByLabel("Upload documents").setInputFiles({
    name: "big.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.alloc(30 * 1024 * 1024, 1),
  });
  await expect(page.getByRole("alert").filter({ hasText: "25 MB" })).toBeVisible();
  expect(uploads).toBe(0);
});

test("failed document actionable", async ({ page }) => {
  await signIn(page);
  // A text file with no extractable content ends in a failed/unsupported state with a reason.
  await page.getByLabel("Upload documents").setInputFiles(textFile(`empty-${Date.now()}.txt`, "\n\n   \n"));
  const row = page.locator('[data-testid="doc-row"]').filter({ has: page.getByRole("alert") }).first();
  await expect(row.getByRole("alert")).toContainText(/./, { timeout: 60_000 });
  await expect(row.getByRole("button", { name: /^Remove/ })).toBeVisible();
});

test("filter and delete", async ({ page }) => {
  await signIn(page);
  const name = `del-${Date.now()}.txt`;
  await page.getByLabel("Upload documents").setInputFiles(textFile(name, `delete me ${Date.now()}\n`));
  const row = page.locator('[data-testid="doc-row"]').filter({ hasText: name });
  await expect(row).toBeVisible();

  const category = page.getByLabel("Category");
  const options = await category.locator("option").allTextContents();
  if (options.length > 1) {
    await category.selectOption({ index: 1 });
    await expect(page.getByRole("list", { name: "Documents" }).or(page.getByText("No matching documents"))).toBeVisible();
    await category.selectOption({ index: 0 });
  }

  await page.getByRole("textbox", { name: "Search titles" }).fill(name);
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: /^Remove/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
  await expect(row).toHaveCount(0);
});
