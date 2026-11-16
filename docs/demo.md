# Demo walkthrough

The recorded demo follows one document through the web UI at `http://localhost:8100/app`: upload, source guide, cited chat, and the metrics page. Each step says what to click and what to say. Two scripted versions of the same story need no clicking: `scripts/demo.sh` is the full story with curl, and `scripts/demo_core.sh` is a shorter smoke run. `make demo-record` plays `frontend/e2e/demo.spec.ts` with video on and writes `frontend/test-results/**/video.webm`.

## Before recording

1. Run `make down`, then `scripts/quickstart.sh` (needs Docker and `OPENAI_API_KEY` in `.env`). On localhost the UI signs you in automatically.
2. Zoom the browser to 125 percent so citations are legible.
3. Choose one contract of a few pages with real clauses. The recording used a public SEC consulting-agreement exhibit from the CUAD set. `make eval-fetch` downloads that set into `eval/sources/cuad_full/`; the file is "MEDALISTDIVERSIFIEDREIT,INC_05_18_2020-EX-10.1-CONSULTING AGREEMENT.PDF" under `Part_II/Consulting Agreements/`. The repository also has a one-paragraph contract, `frontend/e2e/fixtures/services-agreement.txt`, which works for a short run.
4. Start with the library empty, or at least with the demo document as the newest upload. A new chat starts on the newest document that is ready.
5. Without a key, start the fake-model stack from the top of `scripts/demo.sh`. It answers only the questions in the scripted demos.

## Walkthrough

| Step | Do | Say |
|---|---|---|
| 1 | Open `/app` on the Library. Drag the contract into the upload area and point at the status chips as they advance. | "Upload documents, get cited summaries, chat with them. Every claim points to the exact sentence it came from. Code copies that sentence from the stored text, so the model cannot invent a quote. Processing runs in the background, and the status is live over server-sent events." |
| 2 | Open the document. Scroll the source guide, then click the first fact's citation. | "The source guide has a summary, category, tags, tone and key facts. Each fact is checked against the text, and clicking it opens the source viewer on the cited sentence." |
| 3 | Under Custom summary, pick a length and tone, type a focus such as "termination", and click Summarise. | "A summary on request, with its own citations. Length, tone and focus change the result." |
| 4 | Click a suggested question under "Ask about this document". Point at the "Chatting with" bar. Hover a citation marker, then click it. | "The chat opens on this document. Answers stream in segments. A hover shows the stored quote, document, version and page, and a click opens the same sentence in context. The mark next to each segment says whether a separate check found the citation supports it." |
| 5 | Ask a question, then a follow-up (see the table below). | "Multi-turn: the follow-up is rewritten into a standalone question, so 'it' resolves to the right clause." |
| 6 | Ask something the document does not contain, such as the consultant's home address. | "When the document does not contain the answer, Vault says so and offers a next step. It does not guess." |
| 7 | Click "Why this answer?" on an earlier answer. | "The explanation is built from reason codes and templates, never by asking the model to explain itself." |
| 8 | Open Metrics in the top bar. Point at time per step, the outcomes chart and the daily allowance. | "Library, processing and chat figures, each with a plain-language note: how long an answer takes, where that time goes, what each question ended in, and how much of today's allowance is used." |
| 9 | Open `docs/evaluation.md` at the results table, then the README feature table. | "Quality is measured on a golden set, and every number names the command that regenerates it. The README maps each requirement in the case study to where it lives and whether it is built." |

Opening the chat's "Chatting with" bar and ticking a second document shows how a chat is widened to several documents. Answers across several documents are weaker than answers inside one (see Limits in the README), so check any cross-document answer before you record it.

## Questions that work

Checked against the consulting agreement above on the committed code. Every cited sentence of these answers was found in the source text.

| Question | A correct answer says |
|---|---|
| Who are the parties to this agreement? | Gunston Consulting, LLC (a Virginia company) and Medalist Diversified REIT, Inc. (a Maryland corporation) |
| When is the agreement effective? | March 1, 2020 |
| Is there a non-compete and for how long? | Yes: during the consultancy and for twelve months after it ends, with or without cause |
| What does the non-compete prevent the consultant from doing? | Owning an interest in, or taking part in the financing, operation or management of, a competitor, and doing similar services for one |
| How can the agreement be terminated? | On death or bankruptcy, for cause by written notice, or on 90 days' written notice |
| Which state's law governs? | Virginia |
| Who is the REIT's CEO? | Thomas E. Messier, from the signature block |
| Summarize this agreement in a few sentences. | A twelve-month term that renews automatically, an annual fee of $200,000, and stock grants as awarded |
| What is the consultant's home address? | It declines: the document does not give one |

For `services-agreement.txt`: the monthly fee is 4,500 dollars, either party may end it on 30 days' written notice, and Delaware law governs.

## Known weak spots on camera

- Small talk ("thanks") gets the "not found in your documents" card, and "what did I ask first?" returns an error message.
- A chat over many documents, or a vague request such as "give me the gist" across several, can miss a document or decline.
- Wording matters. "What does the non-compete prevent the consultant from doing?" is answered; "what exactly is the consultant barred from doing?" returned "not stated".

## Two documents

The optional cross-document segment uses `frontend/e2e/fixtures/services-agreement.txt` and `frontend/e2e/fixtures/pricing-memo.txt`, which state different monthly fees. Ask "Compare the monthly fee in the services agreement and the pricing memo." With a live key the answer shows both quotes in a "Sources disagree" callout. With the fake model, ask "What is the monthly fee?" instead.

## If something goes wrong on camera

- A document stuck before ready: wait for the status to change and cut the pause.
- An answer that declines: say that Vault declines when the documents do not support an answer, then click a suggested question.
- A rate or quota error: wait a minute, because limits are per user.
- The new-version and stale-marker flow (upload a changed copy of the contract under the same file name, then use "Re-ask" on the stale banner) and the library question ("How many documents do I have, and which are ready?", live key only) fit if there is time. `scripts/demo.sh` covers both.
