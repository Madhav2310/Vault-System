import { test, type Page } from "@playwright/test";

// Visual QA capture (S23.03b). Mocked API, no stack: `SHOTS=1 UI_BASE_URL=http://localhost:5199 npx playwright test e2e/screenshots.spec.ts`
// (vite dev server on 5199). Skipped in `make ui-e2e`. Writes PNGs to e2e/screenshots/<scheme>-<size>/.
test.skip(!process.env["SHOTS"], "set SHOTS=1 to capture screenshots");

const SESSION = "55555555-5555-4555-8555-555555555555";
const DOC = "11111111-1111-4111-8111-111111111111";
const DOC2 = "22222222-2222-4222-8222-222222222222";
const CHUNK = "33333333-3333-4333-8333-333333333333";
const CHUNK_B = "77777777-7777-4777-8777-777777777777";
const RUN = "66666666-6666-4666-8666-666666666666";
const CITED = "Either party may terminate on thirty days notice.";
const TEXT = `The term is two years. ${CITED} Fees are due monthly.`;
const ev = (chunk: string, title: string, text: string) => ({
  chunk_id: chunk, document_id: DOC, document_title: title, version_no: 1, pages: [4, 4], sentence_start: 1, sentence_end: 2, cited_text: text,
});
const evidence = ev(CHUNK, "Master services agreement", CITED);
const frame = (type: string, data: object) => `event: ${type}\ndata: ${JSON.stringify({ run_id: RUN, ...data })}\n\n`;
const done = (type = "answer", content = "Either side can leave on 30 days notice.", extra = "") =>
  frame("done", { message_id: "m-a1", response_type: type, content, reason_codes: [], usage: { input_tokens: 1, output_tokens: 1, cost_usd: 0.01 }, latency_ms: 900, ttft_ms: 300 }).replace('"latency_ms":900', `"latency_ms":900${extra}`);
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const session = { id: SESSION, title: "Termination", document_ids: null, created_at: "2026-11-13T10:00:00Z", updated_at: "2026-11-13T10:00:00Z" };
const verMeta = (id: string, status: string, extra = {}) => ({ id, version_no: 1, status, page_count: 12, created_at: "2026-11-13T10:00:00Z", ready_at: "2026-11-13T10:01:00Z", error_code: null, ...extra });
const docs = [
  { id: DOC, title: "Master services agreement", status: "enriched", category: "contract", tags: ["msa", "legal"], summary_snippet: null, created_at: null, version: verMeta("v1", "enriched") },
  { id: DOC2, title: "Pricing memo", status: "enriched", category: "memo", tags: [], summary_snippet: null, created_at: null, version: verMeta("v2", "enriched", { page_count: 3 }) },
  { id: "d3", title: "Scanned appendix", status: "failed", category: null, tags: [], summary_snippet: null, created_at: null, version: verMeta("v3", "failed", { error_code: "no_text" }) },
];

const answer = [
  frame("run.started", { message_id: "m-u1" }),
  frame("segment", { index: 0, text: "Either side can leave on 30 days notice.", support: "cited", evidence: [evidence] }),
  frame("segment", { index: 1, text: "It is a standard term.", support: "none", evidence: [] }),
  frame("segment", { index: 2, text: "Fees rise yearly.", support: "cited", evidence: [evidence] }),
  frame("verification", { verdicts: [{ index: 0, verdict: "supported" }, { index: 1, verdict: "supported" }, { index: 2, verdict: "unsupported" }] }),
  frame("followups", { questions: ["What is the notice period for non-payment?"] }),
  done(),
].join("");
const conflictBody = {
  topic: "Monthly fee",
  side_a: [ev(CHUNK, "Services Agreement", "The Services Agreement sets the monthly fee at 4,500 dollars.")],
  side_b: [ev(CHUNK_B, "Pricing Memo", "The Pricing Memo states a monthly fee of 5,200 dollars.")],
};
const conflictStream = [
  frame("run.started", { message_id: "m-u1" }),
  frame("segment", { index: 0, text: "The sources differ about the monthly fee.", support: "cited", evidence: [conflictBody.side_a[0]] }),
  frame("verification", { verdicts: [{ index: 0, verdict: "supported" }] }),
  done("answer", "The sources differ about the monthly fee.", `,"conflicts":${JSON.stringify([conflictBody])}`),
].join("");
const abstain = [frame("run.started", { message_id: "m-u1" }), frame("followups", { questions: ["What documents do I have?"] }), done("abstain", "I could not find this in your 2 documents. Try asking in different words, or name the document you have in mind.")].join("");
const clarify = [
  frame("run.started", { message_id: "m-u1" }),
  frame("segment", { index: 0, text: "More than one of your documents could answer this. Which one should I look in?", support: "none", evidence: [] }),
  frame("clarify", { question: "More than one of your documents could answer this. Which one should I look in?", options: [{ label: "Master services agreement", document_id: DOC }, { label: "Pricing memo", document_id: DOC2 }] }),
  done("clarify", "More than one of your documents could answer this. Which one should I look in?").replace('"m-a1"', '"m-c1"'),
].join("");

async function mock(page: Page, o: { stream?: string; status?: number } = {}) {
  await page.route("**/v1/session", (r) => r.fulfill({ status: 204 }));
  await page.route("**/v1/documents?**", (r) => r.fulfill(json({ items: docs, next_cursor: null })));
  await page.route("**/v1/chat/sessions?**", (r) => r.fulfill(json({ items: [session, { ...session, id: "s2", title: "Pricing questions" }], next_cursor: null })));
  await page.route("**/v1/chat/sessions", (r) => (r.request().method() === "POST" ? r.fulfill(json(session, 201)) : r.fulfill(json({ items: [session], next_cursor: null }))));
  await page.route(`**/v1/chat/sessions/${SESSION}`, (r) => r.fulfill(json(session)));
  await page.route(`**/v1/chat/sessions/${SESSION}/messages**`, (r) => {
    if (r.request().method() === "GET") return r.fulfill(json({ items: [], next_cursor: null }));
    if (o.status) return r.fulfill(json({ type: "about:blank", title: "x", status: o.status, code: "internal", detail: null, retryable: true }, o.status));
    return r.fulfill({ status: 200, contentType: "text/event-stream", body: o.stream ?? answer });
  });
  await page.route("**/v1/feedback", (r) => r.fulfill(json({ id: "f", message_id: "m-a1", rating: "down", reason: null, segment_ordinal: null, note: null })));
  await page.route("**/v1/chunks/*", (r) =>
    r.fulfill(json({ id: CHUNK, document_id: DOC, ordinal: 0, kind: "text", text: TEXT, sentences: [[0, 22], [23, 23 + CITED.length], [24 + CITED.length, TEXT.length]], page_start: 4, page_end: 4, section_path: ["8"], token_count: 20, version: { id: "v1", version_no: 1, filename: "msa.pdf" } })),
  );
  await page.route(`**/v1/runs/${RUN}`, (r) => r.fulfill(json({ run_id: RUN, status: "succeeded", mode: "rag", route: "rag", response_type: "answer", reason_codes: [], explanation: ["I searched your documents and found 1 relevant passage."], sources: [{ document_title: "Master services agreement", version_no: 1, pages: [4, 4], chunks_used: 1 }], steps: [{ node: "retrieve", status: "ok", duration_ms: 40 }], usage: { calls: 2, input_tokens: 100, output_tokens: 20, cost_usd: 0.0123 } })));
  await page.route("**/v1/documents/**", (r) => {
    const p = new URL(r.request().url()).pathname;
    if (p.endsWith("/brief"))
      return r.fulfill(json({ status: "ready", version_id: "v1", document_type: "contract", category: "contract", tags: ["msa"], summary: "A two-year services agreement with monthly fees and 30 days' notice to end it.", suggested_questions: ["What is the termination notice period?"], facts: [{ field: "termination", value: "30 days notice", status: "verified", evidence: [{ chunk_id: CHUNK, version_no: 1, pages: [4, 4], section_path: ["8"], sentence_start: 1, sentence_end: 2, cited_text: CITED }] }, { field: "governing_law", value: "Delaware", status: "unverified", evidence: [] }], tone: { label: "formal", note: "Precise legal drafting.", status: "verified", evidence: [] }, model: "fake", created_at: null }));
    if (p.endsWith("/summaries")) return r.fulfill(json({ id: "s1", status: "pending", poll: p + "/s1" }, 202));
    return r.fulfill(json({ id: DOC, title: "Master services agreement", versions: [] }));
  });
}

const sizes = [
  { name: "1440-light", w: 1440, h: 900, scheme: "light" as const },
  { name: "1440-dark", w: 1440, h: 900, scheme: "dark" as const },
  { name: "390-mobile", w: 390, h: 844, scheme: "light" as const },
];

for (const sz of sizes) {
  test.describe(sz.name, () => {
    test.use({ viewport: { width: sz.w, height: sz.h }, colorScheme: sz.scheme });
    const shot = (page: Page, n: string) => page.screenshot({ path: `e2e/screenshots/${sz.name}/${n}.png` });
    const ask = async (page: Page, q = "How can the contract end?") => {
      await page.goto("/app/chat");
      await page.getByLabel("Your question").fill(q);
      await page.getByRole("button", { name: "Ask" }).click();
    };
    const settle = (page: Page) => page.waitForTimeout(400); // screenshot capture only, never in a spec assertion

    test("chat states", async ({ page }) => {
      await mock(page);
      await page.goto("/app/chat");
      await page.getByTestId("suggestions").waitFor();
      await shot(page, "01-empty");
      await ask(page);
      await page.getByTestId("citation-marker").first().waitFor();
      await settle(page);
      await shot(page, "02-answer");
      await page.getByTestId("feedback-down").click();
      await page.getByTestId("reason-wrong").click();
      await page.getByTestId("wrong-doc-nudge").waitFor();
      await shot(page, "03-feedback");
    });
    test("conflict", async ({ page }) => {
      await mock(page, { stream: conflictStream });
      await ask(page, "What is the monthly fee?");
      await page.getByTestId("conflict").waitFor();
      await settle(page);
      await shot(page, "04-conflict");
    });
    test("abstain", async ({ page }) => {
      await mock(page, { stream: abstain });
      await ask(page);
      await page.getByTestId("abstain-card").waitFor();
      await settle(page);
      await shot(page, "05-abstain");
    });
    test("clarify", async ({ page }) => {
      await mock(page, { stream: clarify });
      await ask(page, "What is the notice period?");
      await page.getByTestId("clarify-chips").waitFor();
      await settle(page);
      await shot(page, "06-clarify");
    });
    test("error", async ({ page }) => {
      await mock(page, { status: 500 });
      await ask(page);
      await page.getByTestId("chat-error").waitFor();
      await shot(page, "07-error");
    });
    test("library and document", async ({ page }) => {
      await mock(page);
      await page.goto("/app/");
      await page.getByTestId("doc-row").first().waitFor();
      await shot(page, "08-library");
      await page.goto(`/app/documents/${DOC}`);
      await page.getByTestId("doc-title").waitFor();
      await settle(page);
      await shot(page, "09-document");
    });
  });
}
