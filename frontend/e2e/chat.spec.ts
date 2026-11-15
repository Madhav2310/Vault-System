import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

// Run by `make ui-e2e`. The API is mocked with page.route (deterministic, no fixtures).
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
  /** Streams for the 1st, 2nd, ... message POST (each falls back to `stream`). */
  streams?: string[];
  postStatus?: { status: number; body: object; headers?: Record<string, string> };
  history?: unknown[];
  /** Filled with the JSON body of every message POST and every feedback POST. */
  posts?: Record<string, unknown>[];
  feedback?: Record<string, unknown>[];
}

async function mock(page: Page, o: Opts = {}) {
  let call = 0;
  await page.route("**/v1/session", (r) => r.fulfill({ status: 204 })); // session cookie probe
  await page.route("**/v1/documents?**", (r) => r.fulfill(json({ items: [], next_cursor: null })));
  await page.route("**/v1/chat/sessions?**", (r) => r.fulfill(json({ items: [session], next_cursor: null })));
  await page.route("**/v1/chat/sessions", (r) =>
    r.request().method() === "POST" ? r.fulfill(json(session, 201)) : r.fulfill(json({ items: [session], next_cursor: null })),
  );
  await page.route(`**/v1/chat/sessions/${SESSION}`, (r) => r.fulfill(json(session)));
  await page.route(`**/v1/chat/sessions/${SESSION}/messages**`, (r: Route) => {
    if (r.request().method() === "GET") return r.fulfill(json({ items: o.history ?? [], next_cursor: null }));
    o.posts?.push(r.request().postDataJSON() as Record<string, unknown>);
    if (o.postStatus) return r.fulfill(json(o.postStatus.body, o.postStatus.status, o.postStatus.headers));
    return r.fulfill({ status: 200, contentType: "text/event-stream", body: o.streams?.[call++] ?? o.stream ?? answerStream });
  });
  await page.route("**/v1/feedback", (r) => {
    const body = r.request().postDataJSON() as Record<string, unknown>;
    o.feedback?.push(body);
    return r.fulfill(json({ id: "f1", message_id: body["message_id"], rating: body["rating"], reason: body["reason"] ?? null, segment_ordinal: null, note: null }));
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

// --- Conflicts, staleness, feedback, quota and the support-check verdict -----------------------------------

const CHUNK_B = "77777777-7777-4777-8777-777777777777";
const FEE_A = "The Services Agreement sets the monthly fee at 4,500 dollars.";
const FEE_B = "The Pricing Memo states a monthly fee of 5,200 dollars.";
const side = (chunk: string, title: string, text: string) => ({
  chunk_id: chunk, document_id: DOC, document_title: title, version_no: 1, pages: null,
  sentence_start: 0, sentence_end: 1, cited_text: text,
});
const conflict = {
  topic: "Monthly fee",
  side_a: [side(CHUNK, "Services Agreement", FEE_A)],
  side_b: [side(CHUNK_B, "Pricing Memo", FEE_B)],
};
const histUser = { id: "m-u1", role: "user", content: "What is the monthly fee?", created_at: "2026-11-13T10:00:00Z", response_type: null, coverage_state: null, staleness: null, run_id: null, segments: [], conflicts: [] };
const histAnswer = (extra: Record<string, unknown> = {}, segments: unknown[] = [{ ordinal: 0, text: "Either side can leave on 30 days notice.", support: "cited", evidence: [evidence] }]) => ({
  id: "m-a1", role: "assistant", content: "Either side can leave on 30 days notice.", created_at: "2026-11-13T10:00:05Z",
  response_type: "answer", coverage_state: null, staleness: null, run_id: RUN, followups: [], conflicts: [], segments, ...extra,
});

test("conflict callout cites both sides", async ({ page }) => {
  const stream = [
    frame("run.started", { message_id: "m-u1" }),
    frame("segment", { index: 0, text: "The sources disagree about the fee.", support: "cited", evidence: [side(CHUNK, "Services Agreement", FEE_A)] }),
    done("answer", "The sources disagree about the fee.").replace('"latency_ms":900', '"latency_ms":900,"conflicts":' + JSON.stringify([conflict])),
  ].join("");
  await mock(page, { stream });
  await ask(page, "What is the monthly fee?");
  const callout = page.getByTestId("conflict");
  await expect(callout).toContainText("Sources disagree");
  await expect(callout).toContainText("Monthly fee");
  const sides = callout.getByTestId("conflict-side");
  await expect(sides).toHaveCount(2);
  await expect(sides.nth(0)).toContainText(FEE_A);
  await expect(sides.nth(0)).toContainText("Services Agreement");
  await expect(sides.nth(1)).toContainText(FEE_B);
  await expect(sides.nth(1)).toContainText("Pricing Memo");
  // The passage cited in the answer keeps its number [1] in the callout; the other side is [2].
  await expect(sides.nth(0).getByTestId("citation-marker")).toHaveText("[1]");
  await expect(sides.nth(1).getByTestId("citation-marker")).toHaveText("[2]");
  await sides.nth(1).getByTestId("citation-marker").click();
  await expect(page.getByTestId("source-viewer")).toBeVisible();
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    const bad = (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]).analyze()).violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(bad.map((v) => v.id), `axe ${scheme}`).toEqual([]);
  }
});

test("conflicts are restored from history", async ({ page }) => {
  await mock(page, { history: [histUser, histAnswer({ conflicts: [conflict] })] });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("conflict-side")).toHaveCount(2);
  await expect(page.getByTestId("conflict")).toContainText(FEE_B);
});

test("stale banner re-asks on the newer version", async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  await mock(page, {
    posts,
    stream: answerStream.replace('"message_id":"m-a1"', '"message_id":"m-a2"'),
    history: [histUser, histAnswer({ staleness: { stale: true, since: "2026-11-13T11:00:00Z", newer_version_no: 2 } })],
  });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("stale-banner")).toContainText("older version");
  await page.getByRole("button", { name: "Re-ask on v2" }).click();
  await expect(page.getByTestId("segment")).toHaveCount(4); // 1 restored + 3 streamed
  expect(posts).toHaveLength(1);
  expect(posts[0]!["reask_of"]).toBe("m-a1");
  expect("content" in posts[0]!).toBe(false);
  expect(typeof posts[0]!["client_message_id"]).toBe("string");
  await expect(page.getByTestId("user-message")).toHaveCount(2); // the original question is shown again
  await expect(page.getByTestId("user-message").last()).toContainText("What is the monthly fee?");
});

test("no stale banner for a current answer", async ({ page }) => {
  await mock(page, { history: [histUser, histAnswer()] });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("citation-marker")).toBeVisible();
  await expect(page.getByTestId("stale-banner")).toHaveCount(0);
});

test("feedback up and down with a reason chip", async ({ page }) => {
  const feedback: Record<string, unknown>[] = [];
  await mock(page, { feedback });
  await ask(page);
  const fb = page.getByTestId("feedback");
  await fb.getByRole("button", { name: "Helpful", exact: true }).click();
  await expect(page.getByTestId("feedback-status")).toContainText("saved");
  expect(feedback).toEqual([{ message_id: "m-a1", rating: "up" }]);
  await expect(fb.getByRole("button", { name: "Helpful", exact: true })).toHaveAttribute("aria-pressed", "true");

  await fb.getByRole("button", { name: "Not helpful" }).click();
  await expect(fb.getByRole("button", { name: "Not helpful" })).toHaveAttribute("aria-pressed", "true");
  await fb.getByTestId("reason-citation").click();
  await expect(fb.getByTestId("reason-citation")).toHaveAttribute("aria-pressed", "true");
  expect(feedback).toHaveLength(3);
  expect(feedback[1]).toEqual({ message_id: "m-a1", rating: "down" });
  expect(feedback[2]).toEqual({ message_id: "m-a1", rating: "down", reason: "unsupported" });
});

test("feedback failure is reported", async ({ page }) => {
  await mock(page);
  await page.route("**/v1/feedback", (r) => r.fulfill(json({ type: "about:blank", title: "Not found", status: 404, code: "not_found", detail: null, retryable: false }, 404)));
  await ask(page);
  await page.getByTestId("feedback").getByRole("button", { name: "Helpful", exact: true }).click();
  await expect(page.getByTestId("feedback")).toContainText("did not save");
});

test("quota exceeded", async ({ page }) => {
  await mock(page, {
    postStatus: {
      status: 429,
      headers: { "Retry-After": String(5 * 3600) },
      body: { type: "about:blank", title: "Quota exceeded", status: 429, code: "quota_exceeded", detail: "Daily cost quota reached", retryable: true },
    },
  });
  await ask(page, "Keep me too");
  const err = page.getByTestId("chat-error");
  await expect(err).toContainText("midnight UTC");
  await expect(err).toContainText("about 5 hours");
  await expect(err).not.toContainText("too quickly");
  await expect(page.getByLabel("Your question")).toHaveValue("Keep me too");
});

test("support-check verdict and reason from history", async ({ page }) => {
  const seg = (ordinal: number, verification: string | null, verification_reason: string | null) => ({
    ordinal, text: `Claim ${ordinal}.`, support: "cited", evidence: [evidence], verification, verification_reason,
  });
  await mock(page, {
    history: [
      histUser,
      histAnswer({}, [
        seg(0, "supported", null),
        seg(1, "partial", "The passage gives 30 days but not the form of notice."),
        seg(2, "unsupported", "The passage does not mention fees rising."),
        seg(3, null, null),
      ]),
    ],
  });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("segment")).toHaveCount(4);
  await expect(page.getByTestId("tag-supported")).toHaveCount(1);
  await expect(page.getByTestId("tag-partial")).toHaveText("partly supported");
  await expect(page.getByTestId("tag-partial")).toHaveAttribute("title", /not the form of notice/);
  await expect(page.getByTestId("tag-unsupported")).toHaveText("not supported by its source");
  await expect(page.getByTestId("tag-unsupported")).toHaveAttribute("title", /does not mention fees rising/);
  await expect(page.getByTestId("segment").nth(3).getByTestId("tag-supported")).toHaveCount(0); // no check ran: no label
});

// --- Chat interaction details ----------------------------------------------------------------------------

const HELD = `
  const realFetch = window.fetch.bind(window);
  window.__ctl = null;
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (window.__hold && init && init.method === "POST" && /\\/messages$/.test(url)) {
      const body = new ReadableStream({
        start(c) {
          window.__ctl = c;
          if (init.signal) init.signal.addEventListener("abort", () => c.error(new DOMException("aborted", "AbortError")));
        },
      });
      return Promise.resolve(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } }));
    }
    return realFetch(input, init);
  };
`;

declare global {
  interface Window {
    __ctl: ReadableStreamDefaultController<Uint8Array> | null;
    __hold?: boolean;
  }
}

/** The next message POST answers with a stream the test controls: `push` sends frames, nothing closes it. */
async function hold(page: Page) {
  await page.addInitScript(`window.__hold = true; ${HELD}`);
  return (text: string) => page.evaluate((t) => window.__ctl!.enqueue(new TextEncoder().encode(t)), text);
}

const firstSegment = [
  frame("run.started", { message_id: "m-u1" }),
  frame("status", { stage: "writing" }),
  frame("segment", { index: 0, text: "Either side can leave on 30 days notice.", support: "cited", evidence: [evidence] }),
].join("");
const verification = frame("verification", { verdicts: [{ index: 0, verdict: "supported" }] });

async function axeClean(page: Page, label: string) {
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    const bad = (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]).analyze()).violations.filter(
      (v) => v.impact === "serious" || v.impact === "critical",
    );
    expect(bad.map((v) => v.id), `axe ${label} ${scheme}`).toEqual([]);
  }
}

test("skeleton, then segments, with citation markers after verification", async ({ page }) => {
  await mock(page);
  const push = await hold(page);
  await ask(page);
  await expect(page.getByTestId("answer-skeleton")).toBeVisible();
  await push(firstSegment);
  await expect(page.getByTestId("answer-skeleton")).toHaveCount(0);
  await expect(page.getByTestId("segment")).toHaveCount(1);
  await expect(page.getByTestId("citation-marker")).toBeHidden(); // not checked yet
  await push(verification);
  await expect(page.getByTestId("citation-marker")).toBeVisible();
});

test("stop keeps the partial answer and marks it stopped", async ({ page }) => {
  await mock(page);
  const push = await hold(page);
  await ask(page);
  await push(firstSegment);
  await expect(page.getByTestId("segment")).toHaveCount(1);
  await page.getByTestId("stop").click();
  await expect(page.getByTestId("stopped-note")).toContainText("Stopped");
  await expect(page.getByTestId("segment")).toHaveCount(1);
  await expect(page.getByTestId("citation-marker")).toBeVisible(); // nothing is left pending
  await expect(page.getByTestId("stop")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ask" })).toBeDisabled(); // composer is empty again
  await expect(page.getByTestId("feedback")).toHaveCount(0);
  await expect(page.getByTestId("regenerate")).toBeVisible();
});

test("Esc stops the answer", async ({ page }) => {
  await mock(page);
  const push = await hold(page);
  await ask(page);
  await push(firstSegment);
  await expect(page.getByTestId("stop")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("stopped-note")).toBeVisible();
});

test("regenerate answers the same question with a new message id", async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  await mock(page, { posts });
  await ask(page);
  await expect(page.getByTestId("regenerate")).toBeVisible();
  await page.getByTestId("regenerate").click();
  await expect.poll(() => posts.length).toBe(2);
  // the server re-asks the stored question and hides the replaced pair (no duplicate turn on reload)
  expect(posts[1]!["reask_of"]).toBe("m-a1");
  expect(posts[1]!["replaces"]).toBe("m-a1");
  expect(posts[1]!["content"]).toBeUndefined();
  expect(posts[1]!["client_message_id"]).not.toBe(posts[0]!["client_message_id"]);
  await expect(page.getByTestId("user-message")).toHaveCount(1);
  await expect(page.getByTestId("assistant-message")).toHaveCount(1);
  await expect(page.getByTestId("regenerate")).toBeVisible();
});

test("edit and resend the last question", async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  await mock(page, { posts });
  await ask(page);
  await page.getByTestId("edit-question").click();
  const box = page.getByLabel("Edit your question");
  await expect(box).toHaveValue("How can the contract end?");
  await expect(box).toBeFocused();
  await box.fill("When can I leave early?");
  await page.getByRole("button", { name: "Resend", exact: true }).click();
  await expect.poll(() => posts.length).toBe(2);
  expect(posts[1]!["content"]).toBe("When can I leave early?");
  expect(posts[1]!["replaces"]).toBe("m-a1");
  await expect(page.getByTestId("user-message")).toHaveCount(1);
  await expect(page.getByTestId("user-message")).toContainText("When can I leave early?");
});

test("keyboard: Enter sends, Shift+Enter adds a line, ArrowUp edits the last question", async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  await mock(page, { posts });
  await page.goto("/app/chat");
  const box = page.getByLabel("Your question");
  await box.fill("first");
  await box.press("Shift+Enter");
  await box.pressSequentially("second");
  await expect(box).toHaveValue("first\nsecond");
  expect(posts).toHaveLength(0);
  await box.press("Enter");
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]!["content"]).toBe("first\nsecond");
  await expect(page.getByTestId("answer")).toBeVisible();
  await expect(box).toHaveValue("");
  await box.focus();
  await box.press("ArrowUp");
  await expect(page.getByLabel("Edit your question")).toHaveValue("first\nsecond");
  await page.keyboard.press("Escape"); // cancels the edit
  await expect(page.getByLabel("Your question")).toHaveValue("");
});

test("copy answer includes numbered citations and quotes", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await mock(page);
  await ask(page);
  await page.getByTestId("copy-answer").click();
  await expect(page.getByTestId("copy-status")).toContainText("Copied");
  const text = await page.evaluate(() => navigator.clipboard.readText());
  expect(text).toContain("Either side can leave on 30 days notice. [1]");
  expect(text).toContain(`[1] Master services agreement, v1, p. 4: "${CITED}"`);
});

test("jump to latest appears after scrolling up", async ({ page }) => {
  const history: unknown[] = [];
  for (let i = 0; i < 10; i++) {
    history.push({ ...histUser, id: `m-u${i}`, content: `Question ${i}` });
    history.push(histAnswer({ id: `m-a${i}` }, [{ ordinal: 0, text: "Long answer. ".repeat(60), support: "none", evidence: [] }]));
  }
  await mock(page, { history });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("user-message")).toHaveCount(10);
  await expect(page.getByTestId("jump-latest")).toHaveCount(0); // history opens at the end
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.getByTestId("jump-latest")).toBeVisible();
  await page.getByTestId("jump-latest").click();
  await expect(page.getByTestId("jump-latest")).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollHeight - window.scrollY - window.innerHeight))
    .toBeLessThan(49);
});

test("reason chips send reason and note; Wrong document opens the scope picker", async ({ page }) => {
  const feedback: Record<string, unknown>[] = [];
  await mock(page, { feedback });
  await ask(page);
  await page.getByTestId("feedback-down").click();
  await page.getByTestId("reason-long").click();
  await expect(page.getByTestId("reason-long")).toHaveAttribute("aria-pressed", "true");
  expect(feedback.at(-1)).toEqual({ message_id: "m-a1", rating: "down", reason: "other", note: "too long" });
  await page.getByTestId("reason-incomplete").click();
  await expect(page.getByTestId("reason-incomplete")).toHaveAttribute("aria-pressed", "true");
  expect(feedback.at(-1)).toEqual({ message_id: "m-a1", rating: "down", reason: "incomplete" });
  await expect(page.getByTestId("wrong-doc-nudge")).toHaveCount(0);
  await page.getByTestId("reason-wrong").click();
  await expect(page.getByTestId("wrong-doc-nudge")).toBeVisible();
  expect(feedback.at(-1)).toEqual({ message_id: "m-a1", rating: "down", reason: "wrong" });
  await page.getByTestId("wrong-doc-nudge").getByRole("link").click();
  await expect(page).toHaveURL(/pick=1/);
  await expect(page.getByTestId("scope-picker")).toHaveAttribute("open", "");
});

test("first-run suggestions come from the documents and send on click", async ({ page }) => {
  const posts: Record<string, unknown>[] = [];
  await mock(page, { posts });
  await page.route("**/v1/documents?**", (r) =>
    r.fulfill(
      json({
        items: [{ id: DOC, title: "Master services agreement", status: "ready", category: null, tags: [], summary_snippet: null, created_at: null, version: {} }],
        next_cursor: null,
      }),
    ),
  );
  await page.goto("/app/chat");
  const chips = page.getByTestId("suggestions").getByRole("button");
  await expect(chips.first()).toHaveText('What is "Master services agreement" about?');
  await chips.first().click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]!["content"]).toBe('What is "Master services agreement" about?');
});

test("follow-up chips stay visible after an answer without follow-ups", async ({ page }) => {
  const stream = [
    frame("run.started", { message_id: "m-u1" }),
    frame("segment", { index: 0, text: "Thirty days.", support: "none", evidence: [] }),
    done(),
  ].join("");
  await mock(page, { stream });
  await ask(page);
  await expect(page.getByTestId("followups").getByRole("button")).toHaveCount(2);
});

test("axe on chat states", async ({ page }) => {
  await mock(page);
  const push = await hold(page);
  await page.goto("/app/chat");
  await expect(page.getByTestId("chat-empty")).toBeVisible();
  await axeClean(page, "empty");
  await page.getByLabel("Your question").fill("How can the contract end?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("answer-skeleton")).toBeVisible();
  await axeClean(page, "skeleton");
  await push(firstSegment);
  await expect(page.getByTestId("segment")).toHaveCount(1);
  await page.getByTestId("stop").click();
  await expect(page.getByTestId("stopped-note")).toBeVisible();
  await axeClean(page, "stopped");
});

test("axe on an answer with reason chips and the nudge", async ({ page }) => {
  await mock(page);
  await ask(page);
  await page.getByTestId("feedback-down").click();
  await page.getByTestId("reason-wrong").click();
  await expect(page.getByTestId("wrong-doc-nudge")).toBeVisible();
  await axeClean(page, "reasons");
});

// The question follows "Wrong document?", and Jump-to-latest is laid out from the composer.
test("Wrong document carries the question into the new chat's composer", async ({ page }) => {
  await mock(page);
  await ask(page, "How can the contract end?");
  await page.getByTestId("feedback-down").click();
  await page.getByTestId("reason-wrong").click();
  await page.getByTestId("wrong-doc-nudge").getByRole("link").click();
  await expect(page).toHaveURL(/pick=1/);
  await expect(page.getByTestId("scope-picker")).toHaveAttribute("open", "");
  await expect(page.getByLabel("Your question")).toHaveValue("How can the contract end?");
});

for (const size of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "phone", width: 390, height: 844 },
]) {
  test(`jump to latest sits just above the composer and the page does not scroll sideways (${size.name})`, async ({ page }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    const history: unknown[] = [];
    for (let i = 0; i < 10; i++) {
      history.push({ ...histUser, id: `m-u${i}`, content: `Question ${i}` });
      history.push(histAnswer({ id: `m-a${i}` }, [{ ordinal: 0, text: "Long answer. ".repeat(60), support: "none", evidence: [] }]));
    }
    await mock(page, { history });
    await page.goto(`/app/chat?s=${SESSION}`);
    await expect(page.getByTestId("user-message")).toHaveCount(10);
    await page.evaluate(() => window.scrollTo(0, 0));
    const jump = page.getByTestId("jump-latest");
    await expect(jump).toBeVisible();
    await page.emulateMedia({ reducedMotion: "reduce" }); // no entrance animation while measuring
    const j = (await jump.boundingBox())!;
    const c = (await page.locator("form.composer").boundingBox())!;
    expect(j.y + j.height).toBeLessThanOrEqual(c.y + 0.5); // never covers the composer
    expect(c.y - (j.y + j.height)).toBeLessThan(24); // and stays attached to it
    expect(j.x).toBeGreaterThanOrEqual(0);
    expect(j.x + j.width).toBeLessThanOrEqual(size.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await axeClean(page, `jump ${size.name}`);
  });
}

test("phone: an answer with a conflict fits the width", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page, { history: [histUser, histAnswer({ conflicts: [conflict] })] });
  await page.goto(`/app/chat?s=${SESSION}`);
  await expect(page.getByTestId("conflict-side")).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await axeClean(page, "phone conflict");
});
