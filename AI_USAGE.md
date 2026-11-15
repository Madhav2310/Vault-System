# AI usage

How AI tools were used to design, build and measure Vault: who did what, how the work was organised so that AI output could be checked, where the AI was wrong and how that was caught, and how the prompts were engineered. Facts here come from the git history, the stored evaluation runs and the author's working notes. History-only files are named with the `git show` command that prints them.

## 1. Tools and roles

| Tool | Role |
|---|---|
| Claude Code, Claude Opus 5.5 | Research, design, the plans, the main session (reviewing and merging lane branches), root-cause analyses, evaluation design and the later audit and cleanup passes. |
| Claude Code, Claude Sonnet 5.5 | Most implementation, in parallel git-worktree lanes, plus many main-line commits. The lane branches are almost entirely Sonnet work. |
| ChatGPT and DeepSeek | Independent reviewers of the design, the plans and experiment results. The author pasted their critiques in, and Claude Code reconciled them before anything changed. They never touched the code. |
| OpenAI API (Responses API, embeddings) | The product runtime and the evaluation judges, through roles in `backend/app/llm/routing.py`: `fast` `gpt-6-luna`, `balanced` `gpt-6.1-terra`, `deep` `gpt-6.1-sol`, `flagship` `gpt-6-astra` (gold audit only), embeddings `text-embedding-3-small`. |

The author set direction, made the product and acceptance decisions, decided the gold audit, labelled the judge-calibration pairs, and by plan rule approved any change to a target threshold. 350 of the 457 commits on `main` carry a Claude co-author trailer: 106 Opus and 244 Sonnet (`git log --format='%(trailers:key=Co-Authored-By,valueonly)' | sort | uniq -c`). The work ran from 2026-11-12 to 2026-11-15.

Not used at runtime: the gateway has an Anthropic adapter, which is dormant. The first design (`git show 2751c85:docs/design.md`) assumed Anthropic models and a DeepSeek second-vendor judge. On 2026-11-13 the runtime moved to OpenAI only, because no Anthropic key was available (commit `3efbe4b`). The DeepSeek judge was never wired: an unused `deepseek_api_key` setting in `backend/app/core/settings.py` is all that remains of it. An earlier version of this file said Claude and DeepSeek ran in the product and as judges. That was wrong and was corrected in `bcd3bbd`.

## 2. How the work was organised

1. **Research and design before code.** The first commit, `2751c85`, holds a 884-line design, six research notes and the plan. Later research reports, written before plan v3, are in history: `git show 76c8048^:"docs/research/reports/Vault rebuild strategy research.md"`.
2. **A plan with acceptance criteria.** Each criterion is checked by exactly one named test, one command with its expected output, or one manual check. A `[T]` target may change only with the author's approval. The first plan had 190 tasks and 756 criteria (`git show 2751c85:docs/plan/README.md`). It was revised four times: a reassessment, a research-driven rebuild, an evaluation rebuild and a final UX slice. When the working docs were removed from the tree, the plan files held 327 tasks and 1,345 criteria, 590 of them ticked. The unticked ones are mostly superseded or optional.
3. **Test-first slices.** Tests were written first, with one commit per task and evidence in a progress log (`git show 76c8048^:docs/status/progress.md`). Shortfalls went to a follow-ups list instead of being hidden.
4. **Project rules that constrain the AI** (`CLAUDE.md`):
   - Every model call goes through `backend/app/llm/gateway.py`.
   - Only routing and settings name models.
   - Citations are handles that code validates.
   - The model returns typed decisions and never control flow, SQL or scope.
   - No document text appears in logs.
   - Every repository method is owner-scoped, with a negative cross-user test for each.
   - Every failure gets a data-backed root cause first. This rule was added in `0d120a5` after the early mistakes in section 3.
   - A behaviour change ships disabled and is enabled only by a paired dev-split evaluation ([docs/evaluation.md](docs/evaluation.md#15-acceptance-rule-for-behaviour-switches)).
5. **Parallel worktree agents.** Independent tasks ran as Sonnet lanes in isolated git worktrees. The main session reviewed each branch, merged it and ran `make ci` on the merged tree. History has 87 merge commits.
6. **Independent review as a step.** At checkpoints a fresh read-only agent reviewed the code. It was told that the authors wrote both the code and its tests, so the tests prove little. Its findings were fixed with regression tests (`d7fb373`, `68c5d8b`).
7. **Cost caps on every live run.** Live tests, spikes and evaluation runs take a sample and a `--max-usd` cap. A run that hits its cap exits with a partial result and freezes nothing. Prices: the first citation spike cost $0.054; the sealed test run cost $3.41.
8. **A fake model for UI and pipeline tests** (`backend/app/llm/fake.py`). The Playwright suite and most tests run without any model call.

## 3. Where the AI was wrong, and how it was caught

The AI wrote both the code and its tests, so the most useful checks were the ones it did not write: measurements, per-call timings, an independent reviewer, and the author reading the raw output.

| What the AI got wrong | How it was caught | Fix |
|---|---|---|
| In the design it claimed that provider-native citations guarantee a relevant span. They guarantee only a valid pointer and exact text. | Re-reading the citation docs while rewriting the design | Application-managed citations, with the design saying what code guarantees and what is only measured |
| A first model list missed the newest model family, because its filter regex only kept older name patterns | The provider's models page named IDs that the list lacked | Routing built on the GPT-6 line |
| It assumed prompt caching was free upside. Every prompt over 1,024 tokens was billed as a 1.25x cache write, including RAG prompts that nobody reuses | Reading `cache_write_tokens` in the spike output, then a probe | An explicit cache mode in the provider |
| `strict_schema` stripped every key named `title` or `default`, including real properties with those names. The unit test avoided both names | Self-review against the schemas still to come | Keyword stripping limited to schema nodes, with a regression test (`f18eaab`) |
| Ingestion passed its own tests but had 16 defects, among them a commit-phase error that looped a job forever, a quadratic sentence splitter, and DOCX text dropped inside tracked changes | An independent read-only reviewer reported 16 findings, none of which the existing tests caught | Fixed with regression tests (`d7fb373`) |
| It concluded that the cross-encoder reranker "hurts" recall. The reranker was timing out in 79% of runs, so the A/B compared fusion order with itself | Per-call timings instead of totals (`8d5356d`) | Thread and batch configuration, a degraded-run guard that aborts evaluations. The cross-encoder was later removed (`bbab1f2`): +495 ms at p50 with no detectable gain |
| It blamed slow extraction of a 10-K on page count | Profiling: pdfplumber's `dedupe_chars` was O(n^2), 93% of the time | A linear replacement: 37 s / 720 MB down to 11 s / 198 MB (`59800c5`) |
| It blamed answer omissions on the 4k-token evidence budget. The cut was the chunk cap `top_n: 8`; median evidence was about 2,300 tokens | Offline per-item diagnosis (`make eval-diagnose`) | The next experiment was chosen from that evidence |
| `answer_rag@6` raised answer_pass by 10 points but broke a guard: `must_not_contain` fell from 1.00 to 0.75. "Be complete" pulled a superseded fee into a versioned answer | Guards are read before the headline | `answer_rag@7` added a scope rule (section 4) |
| The first claim judge vetoed every correct `versioned` answer and penalised naming the document: 24 of 51 train "failures" were grader or gold artifacts | RCA of the first train run (`ff11387`) | Fixed judges, then the stored answers re-judged once ($0.51) |
| The over-strict judge queued 77 of 123 items for the gold audit | Counting the queue | Re-queued with the fixed judge: 69 items |
| Judge examples, and later the default answer, analysis, compare and judge prompts, carried gold wording. `answer_rag@7`'s example was a dev item's gold sentence. The analysis prompt named two test-split documents | A word 5-gram sweep of every prompt against every gold item | `eval/contamination.py`, run in CI. Neutral-example versions became the defaults (`cb8474d`). A clean-example arm showed the leak had not inflated dev (0.746 to 0.763; the leaked item still passed) |
| A re-judge wrote into the shared reports folder by default and silently replaced two earlier re-judged results | Found after the fact; the lost results can only be regenerated from the dataset at an older commit | `eval/rejudge.py` writes beside its input and refuses to overwrite without `--overwrite` |
| It reported "13 of 16 faithfulness drops are judge errors". Reading each segment showed most add a small unsupported detail: a label, a superlative, a unit | Reading each rejected segment against its cited passages | Claim retracted in the notes; the no-unsupported-detail rule went into `answer_rag@9`/`@10` |
| A draft final summary gave dev faithfulness as 0.909. That was dev `abstention_correct`; faithfulness is 0.906 | Review of the final numbers (the working notes record the correction) | Corrected before any committed doc carried it |
| Merged lane work without running lint, and a two-lane merge left an undefined variable that ruff and mypy did not flag | `make ci` failed; the resolved file was then read by hand | Rule: `make ci` after every merge, and conflict regions read by hand |
| A settings validation error printed a middle-truncated API key prefix inside a lane transcript | Reading the transcript | Validation errors never echo input values, with a test (`38dfc8c`); the author was told to consider rotating the key |
| Version switches read from the environment never took effect: strings were not parsed into the integer fields | The A/B round itself (`37a5924`) | A parsing validator and a test |
| Concurrency bugs found by a three-lane race and scalability audit after the freeze:<br>- a crashed stage's takeover was never counted, so it looped forever<br>- the daily quota was checked only at admission (5 concurrent turns spent $3.40 against a $1.00 quota)<br>- a 422 held a quota reservation<br>- a cancelled speculative task swallowed the node deadline<br>- an evaluation worker indexed past the end of the item list<br>- the query-embedding cache was shared across owners (a timing side channel) | Reproducer tests (the quota one first as a strict xfail), then the fix | `9d5b1a6`, `a266dba`, `2ec6696`, `339e30c`, `a888f9f`, `6f86334` |

The same rules also stopped wrong fixes:
- **A stall.** A generate call that produced no token and held a turn for 30 s looked like a side effect of larger evidence. Timings showed both arms stalled in the same second, so it was a provider stall. The fix is a 15 s first-token retry (`61a8198`).
- **Rejected features.** Features that did not pass their paired dev A/B were deleted rather than left behind as switches: plan-first answers, a support check that repairs answers, calculation segments (too few items to decide), number-aware lexical variants, a compare quota, a voice fragment, and a shorter analysis prompt (section 4). Later cleanup also deleted the locate loop, scored abstention, clarify-when-ambiguous and contextual chunk headers. Those were built behind switches that were off and never accepted by an evaluation (`a388fd0`, `93241d2`, `a31cd67`, `2ba72ab`).

## 4. Prompt engineering

Prompts are versioned files in `backend/prompts/<id>@<version>.md`. Front matter names the role, effort, output mode and token cap, so a prompt never picks its model. A change is a new version and needs a paired evaluation. Rejected versions were deleted from the tree and stay in git history. All shipped prompts share three design rules:
- **Inputs are untrusted data.** Each prompt has an explicit "untrusted data" section, inputs sit inside delimiter tags, and the injection stratum tests this with 11 variants.
- **Output is a strict schema.**
- **Code decides.** The model returns handles, refs, ranks or verdicts, and code validates and applies them.

### 4.1 Document chat: the answer prompt (`backend/prompts/answer_rag@12.md`)

Why citations are handles. Evidence reaches the model as numbered sentences (`E3.1`, `E3.2`). The model returns segments `{text, evidence: [handles]}` and is told never to write quotes. `backend/app/chat/citations.py` resolves each handle, copies `cited_text` from the stored sentence, and drops an unknown handle with `CITATION_UNRESOLVED`. So a quotation shown to the user can never be model-written. On the 12-item spike: 0 invalid handles and 15 of 15 segments cited. Citation integrity is 1.00 on every kept run since.

How the prompt evolved (dev split, paired, clustered 95% CIs; the judges of the time):

| Version | Change | Measurement | Outcome |
|---|---|---|---|
| @1 | Seven parts: ground rules, citation rule, format by route, coverage, conflicts, length, history; handles only; abstain when the evidence lacks the answer | Not measured on its own (the harness came after it) | Kept |
| @2, @3 | Follow-up suggestions in the schema; a typed `conflicts` field, with each side validated by code | Unit and contract tests; no separate answer-quality run is recorded | Kept |
| @4, @5 | A plan with required elements; typed calculation segments, with code computing the result from the cited operands | Plan-first +1.7 [0.0, +5.2], but the faithfulness lower bound of -3.0 fails the -2 margin. Calculation 0.500 to 0.500 on 10 items, too few to decide | Deleted |
| @6 | Completeness-first: cover every part asked | answer_pass +10.2 [+3.4, +18.0], but `must_not_contain` 1.00 to 0.75 | Not adopted |
| @7 | @6 plus a scope rule: "every part asked, never more", and the named version only | +8.5 [+1.9, +16.1], 5 gained and 0 lost, guards intact | Adopted |
| @8 | Answer under the evidence's own period label instead of abstaining (from a false-abstention RCA) | Train +2.5, dev -1.7, both noise | Not adopted |
| @9 | Detail discipline: no label, superlative, unit or figure the cited sentence lacks; say what an uncovered document does state | Faithfulness +6.3 on train [+0.9, +12.3], but answer_pass -2.5 / -1.7, with 3 real losses from dropped cited details | Not adopted |
| @10 | @9 plus "keep every cited detail" and no derived counts | Support +4.5 train / +2.7 dev; answer_pass no detectable difference | Adopted |
| @11 | @7 with its leaked example replaced, to measure the contamination | Dev 0.746 to 0.763; the leaked item passes with the clean prompt | Leak test only |
| @12 | @10 with neutral examples and names; no rule changes | All final numbers | Shipped |

What the iterations taught:
- **Completeness and faithfulness pull against each other.** Each rule that improved one was checked against the other and against the `must_not_contain` guard.
- **Rules should limit additions, never removals.** @9 lost cited details until @10 said "these rules limit what you add; they never remove what the evidence states".
- **A 1 to 2 point change is unreadable.** The noise between identical runs is about 2 points of answer_pass, and the dev split detects about 8 to 9 points.

### 4.2 The other prompts

| Prompt | Thought process | Evidence |
|---|---|---|
| `backend/prompts/analyze_query@4.md` (role `fast`) | Rewrites follow-ups into a standalone query and returns document refs (`D2`), never IDs, plus dimensions and one intent. Code may narrow scope but never widen it. @2 added the metadata route. @3, a 25% shorter cache-friendly version, showed no change and was deleted. @4 is @2 with neutral examples, after the contamination check found two test-split document names in @2 | @1: 8 of 8 representative questions on the first live check ($0.0007) |
| `backend/prompts/rerank_listwise@1.md` (role `fast`) | Orders numbered focus windows and returns only passage numbers. Code drops invalid numbers and appends omitted passages in fusion order; on any failure it keeps fusion order (`backend/app/retrieval/listwise.py`). Windows are never evidence. Dot leaders are collapsed in the reranker's copy so that financial-statement rows fit | Against no rerank, same answer prompt: dev +8.5 [0.0, +17.9], train +12.7 [+6.5, +19.8], +$0.0004 per turn. 20 candidates gave +1.7 on dev, because gold sat at fused ranks 11 to 44; hence 50 candidates |
| `backend/prompts/support_check@1.md` (role `fast`) | Judges each segment against its own cited passages only ("when unsure, pick the weaker verdict"). Advisory: it annotates, never blocks. A version that repaired answers lost faithfulness (-4.4) and added latency, so it was deleted | Agreement 0.92 and kappa 0.83 against the faithfulness judge (n = 37) |
| `backend/prompts/answer_compare@3.md` | One segment per dimension per document; code checks for a missing side. A per-entity quota with a repair call was underpowered (+14.3 [0.0, +28.6] on 14 items, support -5.6) and was deleted | Paired dev arm |
| `backend/prompts/brief_extract@3.md`, `backend/prompts/brief_merge@2.md` | Facts are one claim each, cited by handle, with values of at most 8 words. `backend/app/insights/verify.py` checks each value against its cited sentences without a model (dates, amounts, names, token overlap). Tone is one of a fixed set, with a note and one to three evidence handles. @3 trimmed the output (25 facts, shorter fields, `max_tokens` 2500 to 1800) | 56 of 58 facts verified on 3 sample documents ($0.043) |
| `docs/voice.md` | The tone guide for code-written messages. A tone fragment appended to the answer prompts changed nothing measurable (answer_pass 0.0, cost +5%) and was deleted | Paired dev arm |

### 4.3 Judge prompts and their decontamination

- **`backend/prompts/eval_judge_claims@4.md`** (role `deep`) makes one binary decision per required claim against the answer. It compares numbers as quantities, accepts paraphrase and listed variants, and requires a verbatim quote. Code overrules a "yes" whose quote is not in the answer (`eval/judge.py`).
  - claims@1 penalised correct `versioned` answers.
  - @2 fixed that and replaced train-mirroring examples with synthetic ones.
  - @3 added quantity and alias rules. Re-judging the answers flipped one item, so the judge explained almost none of the gap.
  - @4 is @3 with neutral examples.
- **`backend/prompts/eval_judge_support@3.md`** judges each segment against only its own cited passages, and judges facts, not naming.
  - Moving from @1 to @2 raised support_rate from 0.66 to 0.84 on the same answers.
  - @3 is @2 with a train item's example replaced.
  - A separate experimental support judge with table context and shown arithmetic was not adopted. It moved support under 1 point, and its kappa against the human labels was 0.70, against 0.78 for @2. It accepted 0 of 53 number-mutated negatives.
- **`backend/prompts/eval_judge_abstain@1.md`** checks that declining, or correcting a false premise, was right.
- **`backend/prompts/eval_audit_gold@1.md`** drives the independent gold audit (role `flagship`).

The contamination check covers every prompt that a default turn or judge run uses. `cd backend && PYTHONPATH=.. uv run python -m eval.contamination` prints `8 prompts checked, 0 item matches`.

## 5. Evaluation-driven development

- **Golden set** (`eval/datasets/vault_eval/`): 311 live items over 186 source documents in 15 strata, with at most 3 items per document.
  - Public strata are sampled from FinanceBench, QASPER and CUAD.
  - Claude Code wrote the 130 authored items and their 45 fixture documents, for the strata that no public set covers: versioned, conflict, injection, false premise, multi-turn and others.
  - Table-lookup gold is built by code from parsed tables, with model-paraphrased questions.
  - Splits come from a hash of the source document (`eval/splits.py`): train 118, dev 59, test 134.
- **What was audited**, counted from each item's `audit_status` and `eval/datasets/vault_eval/audit_queue.yaml`:

| Split | Status | What happened |
|---|---|---|
| Train | 118 `verified` | The `flagship` model audited all 123 items and queued 69 disagreements. The author decided each one: 55 system wrong, 9 gold wrong (corrected), 5 ambiguous (dropped). 46 of the 69 decisions match the model's proposal and 23 differ. |
| Dev | 7 `verified`, 52 `draft` | Only items that failed under the candidate configurations were reviewed. |
| Test | 134 `draft` | Never read by a person or model before the sealed run. |

  The owner review (`review_2026_11_14` in the same file) took 18 disputed train and dev items under a fixed rule: change gold only if the source does not support the claim, the claim is a verbatim-clause artifact, or it asks more than the question. Each item came with the AI's classification and the quoted source. 9 were corrected, 2 AI proposals were rejected and 7 were left unchanged.
- **Paired A/Bs.** Each arm runs on the dev split against one baseline, with items paired by ID, clustered bootstrap CIs, a ledger of gained and lost items, and run fingerprints that refuse to compare unlike runs (`eval/paired.py`, `eval/fingerprint.py`). The acceptance rule is fixed before the measurement (`eval/gate.py`).
- **Three numbers, never mixed:**
  - the locked benchmark on the original train gold (0.699 to 0.772)
  - the audited-gold score
  - the engineering gain with gold and judges fixed (+11.0 train, +10.2 dev)

  A gold correction is never counted as model gain.
- **The sealed test**, run once on the frozen configuration with nothing changed after it: answer_pass 0.761 and faithfulness 0.957 on 134 draft-gold items, with citation integrity 1.00 and 0 of 11 injection violations. Its failures are not read item by item. Every number, run ID and regenerate command is in [docs/evaluation.md](docs/evaluation.md). The final runs are stored in `eval/baselines/final/`.

## 6. Limits of the AI-assisted process

- **The judges share a vendor with the generator.** The current judge versions have not been calibrated against the 200 human-labelled pairs in `eval/labels/labeled.yaml`, so judged numbers are provisional.
- **Most gold is unaudited.** 52 of 59 dev items and all 134 test items are drafts that a model helped write. The gap between test answer_pass (0.761) and dev (0.847) is not diagnosed, because doing so would mean reading test items.
- **The contamination check matches word 5-grams and names, not figures.** `eval_judge_claims@4` still uses figures from two dev items in its examples (a Netflix current-liabilities value and an HR@10 / NDCG@10 pair). Judged results on those two items may be lenient. Fixing that needs a new judge version and a re-judge.
- **Tests come from the same author as the code.** One independent review found 16 defects, and the race audit ran only after the freeze. Other defects of that kind may remain.
- **Most prompt and feature decisions are underpowered.** The dev split detects about 8 to 9 points. "No detectable difference" often means "too few items".
- **Nothing is load-tested.** Concurrency behaviour is designed and unit-tested but not measured under load (`docs/operations.md`).
- **Some evidence is not in the repository.** Many intermediate run IDs and numbers in this file come from the author's working notes and from run reports, and the reports folder is not committed. One abandoned eval turn in about 1,100 has no known cause, because its logs were lost.
