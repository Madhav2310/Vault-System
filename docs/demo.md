# Demo script

A 3 to 4 minute screen recording of the web UI at `http://localhost:8100/app`. Each step says what to click and what to say. The curl version of the same story is `scripts/demo.sh`; `scripts/demo_core.sh` is a shorter smoke run.

## Before recording

1. `make down`, then `scripts/quickstart.sh` (needs Docker and `OPENAI_API_KEY` in `.env`). On localhost the UI signs you in automatically.
2. Zoom the browser to 125 percent so citations are legible.
3. Have two files ready: `frontend/e2e/fixtures/services-agreement.txt` (the contract) and `frontend/e2e/fixtures/pricing-memo.txt` (a memo with a different monthly fee). Start with an empty library.
4. Without a key, start the fake-model stack from the top of `scripts/demo.sh`. It answers only the questions in this script.

To record the walk-through automatically instead of by hand, run `make demo-record` against the running stack. It plays `frontend/e2e/demo.spec.ts` with video on and writes `frontend/test-results/**/video.webm`.

## Script

| Time | Click | Say |
|---|---|---|
| 0:00 | Open `/app` on the empty Library. | "Upload documents, get cited summaries, chat across them. Every claim points to the exact sentence it came from. Code copies that sentence from the stored text, so the model cannot invent a quote." |
| 0:15 | Drag both files into the upload area. Point at the status chips as they advance. | "Processing runs in the background: extract, chunk, embed, index, then a summary. The status is live over server-sent events." |
| 0:40 | Open the contract. Scroll the source guide, then click the first fact's citation. | "Insights: summary, category, tags, key facts, suggested questions. Each fact is checked against the text, and clicking it opens the source viewer on the cited sentence." |
| 1:10 | Go to Chat. Ask `What is the notice period for termination?` Hover a citation marker, then click it. | "Answers stream in segments. A hover shows the stored quote, document, version and page. A click opens the same sentence in context. A mark next to each segment says whether a separate check found the citation supports it." |
| 1:50 | Ask `And which law governs it?` | "Multi-turn: the follow-up is rewritten into a standalone question, so 'it' resolves to the contract." |
| 2:10 | Ask `What is the monthly fee?` (live key: `Compare the monthly fee in the services agreement and the pricing memo.`) | "A cross-document question retrieves from each document. The two disagree, so the answer shows both quotes in a 'Sources disagree' callout instead of one confident number." |
| 2:45 | Ask `What is the CEO's favourite colour?` | "When the documents do not contain the answer, Vault says so and offers a next step. It does not guess." |
| 3:05 | Click "Why this answer?" on an earlier answer. | "The explanation is built from reason codes and templates, never by asking the model to explain itself." |
| 3:20 | Open Metrics in the top bar, then in a terminal run `curl -fsS -H "Authorization: Bearer $KEY" http://localhost:8100/v1/metrics/documents` (and `/v1/metrics/chat`). | "Document counts, processing times, chat latency, time to first token, cost per turn and feedback, from the API the page reads." |
| 3:40 | Open `docs/evaluation.md` at the results table. | "Quality is measured on a golden set with its own report. Every number names the command that regenerates it, and the doc lists what is not met." |
| 3:55 | Close on the README feature table. | "One command runs everything. The README maps every requirement in the case study to where it lives and whether it is built." |

## If something goes wrong on camera

- A document stuck before ready: wait for the status to change and cut the pause.
- An answer that declines: say that it declines when the documents do not support an answer, then click a suggested question.
- A rate or quota error: wait a minute; limits are per user.
- The new-version and stale-marker flow (upload a changed copy of the contract under the same file name, then use "Re-ask" on the stale banner) and the library question ("How many documents do I have, and which are ready?", live key only) fit if there is time; `scripts/demo.sh` covers both.
