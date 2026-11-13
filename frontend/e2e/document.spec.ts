import { expect, test, type Page } from "@playwright/test";

// Written for S19.04; run by `make ui-e2e` (S19.02). The API is mocked with page.route so these specs are
// deterministic and need no fixtures; the app itself is served by the real stack.
const DOC = "11111111-1111-4111-8111-111111111111";
const FLOW_DOC = "22222222-2222-4222-8222-222222222222";
const CHUNK = "33333333-3333-4333-8333-333333333333";
const FLOW_CHUNK = "44444444-4444-4444-8444-444444444444";

const S1 = "The term is two years.";
const TERMINATE = "Either party may terminate on thirty days notice.";
const S3 = "Fees are due monthly.";
const TEXT = `${S1} ${TERMINATE} ${S3}`;
// [start, end) offsets of the three sentences, computed from the text (all ASCII)
const SENTENCES = [
  [0, S1.length],
  [S1.length + 1, S1.length + 1 + TERMINATE.length],
  [S1.length + TERMINATE.length + 2, TEXT.length],
];

const cite = (chunk: string, pages: number[]) => ({
  chunk_id: chunk,
  version_no: 1,
  pages,
  section_path: ["8", "Termination"],
  sentence_start: 1,
  sentence_end: 2,
  cited_text: TERMINATE,
});

function brief(chunk: string, pages: number[]) {
  return {
    status: "ready",
    version_id: "v1",
    document_type: "contract",
    category: "contract",
    tags: ["msa"],
    summary: "A services agreement.",
    suggested_questions: ["What is the termination notice period?"],
    facts: [
      { field: "termination", value: "30 days notice", status: "verified", evidence: [cite(chunk, pages)] },
      { field: "governing_law", value: "Delaware", status: "unverified", evidence: [cite(chunk, pages)] },
    ],
    model: "fake",
    created_at: null,
  };
}

async function mock(page: Page, opts: { doc: string; chunk: string; pages: number[] }) {
  await page.addInitScript(() => localStorage.setItem("vault.apiKey", "vk_test_demo"));
  const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/v1/documents/**", (r) => {
    const url = new URL(r.request().url());
    const path = url.pathname;
    if (path.endsWith("/brief")) return r.fulfill(json(brief(opts.chunk, opts.pages)));
    if (/\/summaries\/[^/]+$/.test(path)) {
      return r.fulfill(
        json({
          id: "s1",
          status: "ready",
          segments: [{ ordinal: 0, text: "Either side can leave on notice.", support: "cited", citations: [cite(opts.chunk, opts.pages)] }],
        }),
      );
    }
    if (path.endsWith("/summaries")) return r.fulfill(json({ id: "s1", status: "pending", poll: path + "/s1" }, 202));
    return r.fulfill(json({ id: opts.doc, title: "Master services agreement", versions: [] }));
  });
  await page.route("**/v1/chunks/*", (r) =>
    r.fulfill(
      json({
        id: opts.chunk,
        document_id: opts.doc,
        ordinal: 4,
        kind: "paragraph",
        text: TEXT,
        sentences: SENTENCES,
        page_start: opts.pages[0],
        page_end: opts.pages[1],
        section_path: ["8", "Termination"],
        token_count: 20,
        version: { id: "v1", version_no: 1, filename: "msa.pdf" },
      }),
    ),
  );
}

test("fact citation highlights sentence", async ({ page }) => {
  await mock(page, { doc: DOC, chunk: CHUNK, pages: [3, 3] });
  await page.goto(`/app/documents/${DOC}`);
  await page.getByRole("button", { name: /Citation 1/ }).first().click();
  const viewer = page.getByTestId("source-viewer");
  await expect(viewer.getByRole("heading", { name: "Master services agreement" })).toBeVisible();
  await expect(viewer.getByTestId("cited-sentence")).toHaveCount(1);
  await expect(viewer.getByTestId("cited-sentence")).toHaveText(TERMINATE);
  await expect(viewer).toContainText(/Page\s*3/);
  await expect(viewer).toContainText("8 › Termination");
});

test("unverified labelled", async ({ page }) => {
  await mock(page, { doc: DOC, chunk: CHUNK, pages: [3, 3] });
  await page.goto(`/app/documents/${DOC}`);
  const facts = page.getByTestId("fact");
  await expect(facts.filter({ hasText: "Delaware" }).getByText("unverified")).toBeVisible();
  await expect(facts.filter({ hasText: "30 days notice" }).getByText("unverified")).toHaveCount(0);
});

test("suggested question starts chat", async ({ page }) => {
  await mock(page, { doc: DOC, chunk: CHUNK, pages: [3, 3] });
  await page.goto(`/app/documents/${DOC}`);
  await page.getByRole("link", { name: "What is the termination notice period?" }).click();
  await expect(page).toHaveURL(/\/chat\?/);
  const params = new URL(page.url()).searchParams;
  expect(params.get("doc")).toBe(DOC);
  expect(params.get("q")).toBe("What is the termination notice period?");
  // TODO(S19.05): once the chat screen exists, assert that the question was sent and answered.
  await expect(page.getByTestId("chat-q")).toHaveText("What is the termination notice period?");
});

test("custom summary", async ({ page }) => {
  await mock(page, { doc: DOC, chunk: CHUNK, pages: [3, 3] });
  await page.goto(`/app/documents/${DOC}`);
  await page.getByLabel("Length").selectOption("short");
  await page.getByLabel("Tone").selectOption("plain");
  await page.getByLabel("Focus (optional)").fill("termination");
  await page.getByRole("button", { name: "Summarise" }).click();
  await expect(page.getByTestId("summary-pending")).toBeVisible();
  const seg = page.getByTestId("summary-segment");
  await expect(seg).toContainText("Either side can leave on notice.", { timeout: 10_000 });
  await expect(seg.getByRole("button", { name: /Citation 1/ })).toBeVisible();
  await seg.getByRole("button", { name: /Citation 1/ }).click();
  await expect(page.getByTestId("source-viewer").getByTestId("cited-sentence")).toHaveText(TERMINATE);
});

test("no page zero", async ({ page }) => {
  await mock(page, { doc: FLOW_DOC, chunk: FLOW_CHUNK, pages: [0, 0] });
  await page.goto(`/app/documents/${FLOW_DOC}`);
  await page.getByRole("button", { name: /Citation 1/ }).first().click();
  const viewer = page.getByTestId("source-viewer");
  await expect(viewer.getByTestId("cited-sentence")).toHaveText(TERMINATE);
  await expect(viewer).not.toContainText(/page\s*0/i);
  await expect(viewer.getByText("Page", { exact: true })).toHaveCount(0);
});
