# Demo narration script (3 to 5 minutes)

A script for a screen recording of the web UI at `http://localhost:8100/app`. It follows the same story as `scripts/demo.sh`, which does every step with curl and prints the spend. After recording, add the link to the README (`Quickstart` section, "Screen recording").

## Before you record

- Fresh stack: `make down`, then `scripts/quickstart.sh`. Copy the API key it prints. Open `/app` and paste the key.
- Documents to upload, from the repo: `frontend/e2e/fixtures/services-agreement.txt` (the contract), `frontend/e2e/fixtures/pricing-memo.txt` (a memo that changes the commercial terms) and `eval/datasets/vault_eval_v1/docs/amendment_consulting.md` (an amendment). A 10-K excerpt PDF works too (pass your own file).
- A second copy of the contract with one extra line, saved under the same file name in another folder, for the new-version step: it must differ in bytes or it is deduplicated.
- Terminal window ready for the last step, with `API` and `KEY` set (see the README "Feature tour").
- No model key and no spend: start the fake-model stack from the top of `scripts/demo.sh` and use only the scripted questions below (the meta question is not scripted).
- Close other windows. Zoom the browser to 125 percent so citations are readable.

## Script

Times are targets, not limits. Say the quoted lines in your own words.

### 0:00 The idea (15 s)
On the library page, empty. "This is Vault. You upload documents, and you chat with them. The one thing I care about is that every claim points at the exact sentence it came from, and that sentence is copied by the code, so the model can't invent a quote."

### 0:15 Upload and live status (30 s)
Drag in the three files. "Three documents: a contract, a memo that changes its terms, and an amendment." Point at the status chips changing. "Processing runs in the background: extract, chunk, embed, index, then a summary. I can keep working while it runs."

### 0:45 Source guide (30 s)
Open the contract when it is ready. "Each document gets a source guide: what it is, a summary, key facts, and suggested questions. Every fact is cited and checked against the text." Click a fact to open the source viewer at the cited sentence. "This is the original text, not a rewrite."

### 1:15 A lookup question, streamed (40 s)
Open chat. Click the suggested question or type: "What is the notice period for termination?" "Watch the status: analyzing, retrieving, checking evidence, writing." When the answer streams in: "Each sentence has a citation number." Hover one, then click it. "The quote is the stored sentence, with the document, version and page."

### 1:55 A follow-up (25 s)
Type: "And which law governs it?" "It knows 'it' means the agreement. The session remembers the conversation, and the scope is still all three documents."

### 2:20 Compare across documents (35 s)
Type: "What is the monthly fee?" (live: "Compare the monthly fee in the services agreement and the pricing memo.") "Two documents give different fees. The answer cites both, so I can see the disagreement instead of getting one confident number." Say plainly that a dedicated compare and conflict route is still on the roadmap; today this is an ordinary multi-document question.

### 2:55 A meta question (15 s, live key only)
Type: "How many documents do I have, and which are ready?" "This one isn't about document text. It's answered from my library metadata with a fixed, owner-scoped query, so it can't leak anyone else's data." Skip this step on the fake stack.

### 3:10 Why this answer (25 s)
Click "Why this answer?" on the first answer. "The explanation is built from reason codes and templates, not from asking the model to explain itself. It shows the route, the sources used and the steps." 

### 3:35 A new version and the stale marker (30 s)
Upload the changed contract as a new version of the same document. "When a newer version becomes ready, answers that cited the old one get marked." Show the banner on the notice-period answer and click "Re-ask". "One click asks the same question again against the latest version."

### 4:05 Metrics and cost (30 s)
Switch to the terminal and run the two metrics calls from the README. "Turns, outcomes, latency and time to first token, cost per turn. The whole demo is a few cents on a real key, and the same story is scripted in `scripts/demo.sh`, which prints the spend from the ledger."

### 4:35 Close (15 s)
"Everything here runs from one command. The README lists what works, what is measured and what isn't, and what I chose not to build."

## If something goes wrong on camera
- A document stuck before ready: say so, wait for the status to change, cut the pause in editing.
- An answer abstains: that is the evidence gate working. Say "it declines when the documents don't support an answer", then ask the suggested question instead.
- Rate or quota error: wait a minute; the limits are per user.
