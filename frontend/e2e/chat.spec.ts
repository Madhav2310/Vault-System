import { expect, test, type Page, type Route } from "@playwright/test";

// Written for S19.05; run by `make ui-e2e`. The API is mocked with page.route (deterministic, no fixtures).
const SESSION = "55555555-5555-4555-8555-555555555555";
const DOC = "11111111-1111-4111-8111-111111111111";
const CHUNK = "33333333-3333-4333-8333-333333333333";
const RUN = "66666666-6666-4666-8666-666666666666";
const CITED = "Either party may terminate on thirty days notice.";
const TEXT = `The term is two years. ${CITED} Fees are due monthly.`;

const evidence = {
  chunk_id: CHUNK,
  document_id: DOC,
  document_title: "Master services agreement",
  version_no: 1,
  pages: [4, 4],
  sentence_start: 1,
  sentence_end: 2,
  cited_text: CITED,
};

const frame = (type: string, data: object) => `event: ${type}\ndata: ${JSON.stringify({ run_id: RUN, ...data })}\n\n`;
const done = (response_type = "answer", content = "Either side can leave on 30 days notice.") =>
  frame("done", {
    message_id: "m-a1",
    response_type,
    content,
    reason_codes: [],
    usage: { input_tokens: 100, output_tokens: 20, cost_usd: 0.0123 },
    latency_ms: 900,
    ttft_ms: 300,
  });

const answerStream = [
  frame("run.started", { message_id: "m-u1" }),
  frame("status", { stage: "retrieving" }),
  frame("status", { stage: "writing" }),
  frame("segment", { index: 0, text: "Either side can leave on 30 days notice.", support: "cited", evidence: [evidence] }),
  frame("segment", { index: 1, text: "It is a standard term.", support: "none", evidence: [] }),
  frame("segment", { index: 2, text: "Fees rise yearly.", support: "cited", evidence: [evidence] }),
  frame("verification", {
    verdicts: [
      { index: 0, verdict: "supported" },
      { index: 1, verdict: "supported" },
      { index: 2, verdict: "unsupported" },
    ],
  }),
  frame("followups", { questions: ["What is the notice period for non-payment?"] }),
  done(),
].join("");

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) => ({
  status,
  contentType: "application/json",
  headers,
  body: JSON.stringify(body),
});

const session = { id: SESSION, title: "Termination", document_ids: null, created_at: "2026-11-13T10:00:00Z", updated_at: "2026-11-13T10:00:00Z" };

interface Opts {
  stream?: string;
  postStatus?: { status: number; body: object; headers?: Record<string, string> };
  history?: unknown[];
}

async function mock(page: Page, o: Opts = {}) {
  await page.addInitScript(() => localStorage.setItem("vault.apiKey", "vk_test_demo"));
  await page.route("**/v1/documents?**", (r) => r.fulfill(json({ items: [], next_cursor: null })));
  await page.route("**/v1/chat/sessions?**", (r) => r.fulfill(json({ items: [session], next_cursor: null })));
  await page.route("**/v1/chat/sessions", (r) =>
    r.request().method() === "POST" ? r.fulfill(json(session, 201)) : r.fulfill(json({ items: [session], next_cursor: null })),
  );
  await page.route(`**/v1/chat/sessions/${SESSION}`, (r) => r.fulfill(json(session)));
  await page.route(`**/v1/chat/sessions/${SESSION}/messages**`, (r: Route) => {
    if (r.request().method() === "GET") return r.fulfill(json({ items: o.history ?? [], next_cursor: null }));
    if (o.postStatus) return r.fulfill(json(o.postStatus.body, o.postStatus.status, o.postStatus.headers));
    return r.fulfill({ status: 200, contentType: "text/event-stream", body: o.stream ?? answerStream });
  });
  await page.route("**/v1/chunks/*", (r) =>
    r.fulfill(
      json({
        id: CHUNK, document_id: DOC, ordinal: 0, kind: "text", text: TEXT,
        sentences: [[0, 22], [23, 23 + CITED.length], [24 + CITED.length, TEXT.length]],
        page_start: 4, page_end: 4, section_path: ["8"], token_count: 20,
        version: { id: "v1", version_no: 1, filename: "msa.pdf" },
      }),
    ),
  );
  await page.route(`**/v1/documents/${DOC}`, (r) => r.fulfill(json({ id: DOC, title: "Master services agreement", versions: [] })));
  await page.route(`**/v1/runs/${RUN}`, (r) =>
    r.fulfill(
      json({
        run_id: RUN, status: "succeeded", mode: "rag", route: "rag", response_type: "answer", reason_codes: [],
        explanation: ["I searched your documents and found 1 relevant passage."],
        sources: [{ document_title: "Master services agreement", version_no: 1, pages: [4, 4], chunks_used: 1 }],
        steps: [{ node: "retrieve", status: "ok", duration_ms: 40 }],
        usage: { calls: 2, input_tokens: 100, output_tokens: 20, cost_usd: 0.0123 },
      }),
    ),
  );
}

async function ask(page: Page, q = "How can the contract end?") {
  await page.goto("/app/chat");
  await page.getByLabel("Your question").fill(q);
  await page.getByRole("button", { name: "Ask" }).click();
}

test("streams segments", async ({ page }) => {
  await mock(page);
  await ask(page);
  await expect(page.getByTestId("segment")).toHaveCount(3);
  await expect(page.getByTestId("followups").getByRole("button")).toHaveText("What is the notice period for non-payment?");
  await expect(page.getByTestId("status-line")).toHaveCount(0); // gone once the turn is done
});

test("hover and click citation", async ({ page }) => {
  await mock(page);
  await ask(page);
  const marker = page.getByTestId("citation-marker").first();
  await marker.hover();
  await expect(page.getByTestId("citation-card")).toContainText(CITED);
  await expect(page.getByTestId("citation-card")).toContainText("Master services agreement");
  await marker.click();
  await expect(page.getByTestId("source-viewer")).toBeVisible();
  await expect(page.getByTestId("cited-sentence")).toHaveText(CITED);
});

test("trust labels", async ({ page }) => {
  await mock(page);
  await ask(page);
  await expect(page.getByTestId("tag-uncited")).toHaveText("no citation");
  const bad = page.getByTestId("tag-unsupported");
  await expect(bad).toHaveText("not supported by its source");
  await expect(bad).toHaveAttribute("title", /does not back this statement/);
});

test("abstention card", async ({ page }) => {
  const stream = [
    frame("run.started", { message_id: "m-u1" }),
    frame("followups", { questions: ["What documents do I have?"] }),
    done("abstain", "I could not find that in your documents."),
  ].join("");
  await mock(page, { stream });
  await ask(page);
  await expect(page.getByTestId("abstain-card")).toContainText("Not found in your documents");
  await expect(page.getByTestId("followups")).toContainText("What documents do I have?");
});

test("why panel", async ({ page }) => {
  await mock(page);
  await ask(page);
  await page.getByRole("button", { name: "Why this answer?" }).click();
  await expect(page.getByTestId("why-explanation")).toContainText("found 1 relevant passage");
  await expect(page.getByTestId("why-cost")).toContainText("$0.0123");
});

test("rate limited", async ({ page }) => {
  await mock(page, {
    postStatus: {
      status: 429,
      headers: { "Retry-After": "30" },
      body: { type: "about:blank", title: "Rate limited", status: 429, code: "rate_limited", detail: null, retryable: true },
    },
  });
  await ask(page, "Keep this text");
  await expect(page.getByTestId("chat-error")).toContainText("30 seconds");
  await expect(page.getByLabel("Your question")).toHaveValue("Keep this text");
});

test("history restored", async ({ page }) => {
  await mock(page, {
    history: [
      { id: "m-u1", role: "user", content: "How can the contract end?", created_at: "2026-11-13T10:00:00Z", response_type: null, coverage_state: null, staleness: null, run_id: null, segments: [] },
      {
        id: "m-a1", role: "assistant", content: "Either side can leave on 30 days notice.", created_at: "2026-11-13T10:00:05Z",
        response_type: "answer", coverage_state: null, staleness: null, run_id: RUN, followups: [],
        segments: [{ ordinal: 0, text: "Either side can leave on 30 days notice.", support: "cited", evidence: [evidence] }],
      },
    ],
  });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("user-message")).toContainText("How can the contract end?");
  await page.getByTestId("citation-marker").click();
  await expect(page.getByTestId("cited-sentence")).toHaveText(CITED);
});
