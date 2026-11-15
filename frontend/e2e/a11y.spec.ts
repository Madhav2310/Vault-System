import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Run by `make ui-e2e` against the fake-LLM stack (real API, scripted model, fixtures from frontend/e2e/fixtures).
const API_KEY = process.env.UI_API_KEY ?? "vk_test_demo";
const QUESTION = "What is the notice period for termination?"; // scripted in backend/tests/fixtures/ui_script.yaml

async function signIn(page: Page) {
  await page.request.post("/v1/session", {
    data: { api_key: API_KEY },
    headers: { "X-Requested-With": "vault-ui" },
  });
}

/** Run axe (WCAG 2.x A/AA + best practices) and return the serious/critical violations. */
async function audit(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"])
    .analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const minor = results.violations.filter((v) => !bad.includes(v)).map((v) => `${v.id}(${v.impact})`);
  console.log(`axe ${label}: ${results.passes.length} rules passed, ${bad.length} serious/critical, minor: [${minor.join(", ")}]`);
  return bad.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map((n) => `${n.target.join(" ")} :: ${n.failureSummary?.split("\n").slice(0, 2).join(" ")}`),
  }));
}

async function openLibrary(page: Page) {
  await page.goto("/app/");
  await expect(page.getByTestId("doc-row").first()).toBeVisible();
}

async function openDocument(page: Page) {
  await openLibrary(page);
  await page.getByTestId("doc-row").first().getByRole("link").click();
  await expect(page.getByTestId("doc-title")).toBeVisible();
  await expect(page.getByText("Loading the source guide…")).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByText("The source guide is being prepared…")).toHaveCount(0, { timeout: 60_000 });
}

async function ask(page: Page, q = QUESTION) {
  await page.goto("/app/chat");
  await page.getByLabel("Your question").fill(q);
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("citation-marker").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("status-line")).toHaveCount(0, { timeout: 60_000 }); // streaming finished
  await expect(page.getByRole("button", { name: "Why this answer?" })).toBeVisible();
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`axe ${scheme}`, () => {
    test.beforeEach(async ({ page }) => {
      // reduced motion: no mid-transition colours for axe to sample
      await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
      await signIn(page);
    });

    test(`library (${scheme})`, async ({ page }) => {
      await openLibrary(page);
      expect(await audit(page, `library ${scheme}`)).toEqual([]);
    });

    test(`document (${scheme})`, async ({ page }) => {
      await openDocument(page);
      expect(await audit(page, `document ${scheme}`)).toEqual([]);
      const cite = page.getByRole("button", { name: /Citation 1/ }).first();
      if (await cite.count()) {
        await cite.click();
        await expect(page.getByTestId("cited-sentence")).toBeVisible();
        expect(await audit(page, `document+source ${scheme}`)).toEqual([]);
      }
    });

    test(`metrics (${scheme})`, async ({ page }) => {
      await page.goto("/app/metrics");
      await expect(page.getByTestId("stat-turns")).toBeVisible();
      await expect(page.getByTestId("stat-jobs")).toBeVisible();
      await expect(page.getByTestId("stat-documents")).toBeVisible();
      expect(await audit(page, `metrics ${scheme}`)).toEqual([]);
    });

    test(`chat (${scheme})`, async ({ page }) => {
      await ask(page);
      expect(await audit(page, `chat ${scheme}`)).toEqual([]);
      await page.getByTestId("citation-marker").first().hover();
      await expect(page.getByTestId("citation-card")).toBeVisible();
      expect(await audit(page, `chat+card ${scheme}`)).toEqual([]);
      await page.getByTestId("citation-marker").first().click();
      await expect(page.getByTestId("cited-sentence")).toBeVisible();
      await page.getByRole("button", { name: "Why this answer?" }).click();
      await expect(page.getByTestId("why-explanation")).toBeVisible();
      expect(await audit(page, `chat+source+why ${scheme}`)).toEqual([]);
    });
  });
}

test("keyboard flow", async ({ page }) => {
  test.setTimeout(150_000);
  await signIn(page);
  const kb = page.keyboard;

  /** Press Tab (never click) until the focused element matches; fails after `max` presses. */
  async function tabTo(match: { label?: string; linkText?: string; tag?: string }, what: string, max = 80) {
    for (let i = 0; i < max; i++) {
      await kb.press("Tab");
      const hit = await page.evaluate((m) => {
        const el = document.activeElement;
        if (!el) return false;
        if (m.label) return el.getAttribute("aria-label") === m.label;
        if (m.linkText) return el instanceof HTMLAnchorElement && (el.textContent ?? "").trim() === m.linkText;
        return el.tagName === m.tag;
      }, match);
      if (hit) return;
    }
    throw new Error(`keyboard: never reached ${what} in ${max} Tab presses`);
  }

  // 1. Upload: reach the file input with Tab, open the chooser with Enter.
  await page.goto("/app/");
  await expect(page.getByTestId("doc-row").first()).toBeVisible();
  const name = `keyboard-${Date.now()}`;
  await tabTo({ label: "Upload documents" }, "the upload control");
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), kb.press("Enter")]);
  await chooser.setFiles({
    name: `${name}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(`Either party may terminate this Agreement on 30 days written notice. ${name}\n`),
  });
  const row = page.getByTestId("doc-row").filter({ hasText: name });
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-status", "enriched", { timeout: 60_000 });

  // 2. Open the document: Tab to its title link, Enter.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await tabTo({ linkText: name }, "the document link");
  await kb.press("Enter");
  await expect(page.getByTestId("doc-title")).toContainText(name);

  // 3. Ask: Tab to the Chat link, Enter, Tab to the question box, type, Enter.
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await tabTo({ linkText: "Chat" }, "the Chat link");
  await kb.press("Enter");
  await expect(page).toHaveURL(/\/chat/);
  await tabTo({ tag: "TEXTAREA" }, "the question box");
  await kb.type(QUESTION);
  await kb.press("Enter");
  await expect(page.getByTestId("citation-marker").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("status-line")).toHaveCount(0, { timeout: 60_000 });

  // 4. Open a citation with Enter; the viewer takes focus.
  await tabTo({ label: "Citation 1: open source" }, "the first citation");
  await expect(page.getByTestId("citation-card")).toContainText(/\w/); // focus shows the exact quote
  await kb.press("Enter");
  const viewer = page.getByTestId("source-viewer");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByTestId("cited-sentence")).toContainText("30 days written notice");
  await expect(viewer.getByRole("heading")).toBeFocused();

  // 5. Close with Escape; focus returns to the citation that opened it.
  await kb.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Citation 1: open source" })).toBeFocused();
});

for (const width of [1280, 768, 320]) {
  test(`no horizontal overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await signIn(page);
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

    await openLibrary(page);
    expect(await overflow(), "library").toBeLessThanOrEqual(0);

    await openDocument(page);
    const cite = page.getByRole("button", { name: /Citation 1/ }).first();
    if (await cite.count()) {
      await cite.click();
      await expect(page.getByTestId("cited-sentence")).toBeVisible();
    }
    expect(await overflow(), "document").toBeLessThanOrEqual(0);

    await ask(page);
    await page.getByTestId("citation-marker").first().click();
    await expect(page.getByTestId("cited-sentence")).toBeVisible();
    expect(await overflow(), "chat").toBeLessThanOrEqual(0);

    await page.goto("/app/metrics");
    await expect(page.getByTestId("stat-turns")).toBeVisible();
    expect(await overflow(), "metrics").toBeLessThanOrEqual(0);
  });
}
