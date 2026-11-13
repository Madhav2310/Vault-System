# AI usage

How AI tools were used to design and build this project, including where they were wrong and how that was caught.

## Tools used
- **Claude Code (Claude Opus 5.5)**: research, design, planning, implementation, tests, eval-set construction.
- **ChatGPT**: independent critique of the design. Its review was reconciled into design v2 (decision log D1–D25 in `docs/design.md`).
- **Claude / OpenAI / DeepSeek APIs**: product runtime (answers, analysis, embeddings) and cross-family eval judges.

## Workflow
1. **Design first.** Design (`docs/design.md`) and research passes (`docs/research/01–06`) were written before any code, then cross-reviewed with ChatGPT.
2. **Plan with checks.** `docs/plan/` holds 194 tasks and 792 acceptance criteria. Each criterion is verified by a named test, a command or a manual check. `scripts/plan_lint.py` checks the plan's consistency.
3. **Build one task at a time.** Tests come first, then one commit per task prefixed with the task ID. Evidence goes in `docs/progress.md`, and shortfalls and postponed work go in `docs/followups.md`.
4. **Claude Code rules.** Project rules in `CLAUDE.md` constrain Claude Code: the gateway-only LLM calls, code-side model routing and citation validation, owner scoping, no content in logs, and the rest.

## Per-slice log
### S0
- S0.01–S0.03: repo init, uv/ruff/mypy/pytest skeleton, logs.
- S0.04–S0.22: compose stack, settings, logging, errors, DB schema, auth, repository pattern, object store, LLM gateway, Celery, OpenTelemetry, health, leak guard, eval wiring and spikes. Two tasks were built by parallel subagents in isolated git worktrees (S0.18+S0.21, S0.13); their branches were reviewed and merged by the main session.
- 2026-11-13 provider change: the runtime moved from Anthropic to OpenAI only (no Anthropic key). Claude Code researched current models, pricing, structured outputs, caching and rate limits, ran sub-cent probes on the real key, and rewrote design §4/§5.3/§8.5/§9.1/§13 and the affected plan tasks (new S0.23–S0.25, S4.19).
- S0.23–S0.25 (same day): the OpenAI Responses provider, model roles with the router and a one-attempt role fallback, and a citation spike on 12 sampled smoke items (17 live calls, $0.054 of a $0.50 cap). Live checks are capped samples; the spike's checks read a saved results file so re-running them costs nothing.

## Corrections
| Date | Task | What the AI got wrong | How it was caught | Fix |
|---|---|---|---|---|
| 2026-11-12 | planning | Renumber tool rewrote range endpoints twice (12 ranges corrupted) | `plan_lint` + manual review | Single-pass regex; ranges repaired by meaning |
| 2026-11-12 | planning | Default pdfplumber extraction merged words and interleaved columns (QASPER quotes unlocatable) | Gold-quote locate rate 86/100 | `use_text_flow=True, x_tolerance_ratio=0.15` → 99/100 |
| 2026-11-12 | S0.02 | `uv lock` was allowed to probe Homebrew interpreters; a broken `python@3.13` crashed (user saw the crash dialog) | User report + crash log RCA | `python-preference = "only-managed"` |
| 2026-11-13 | provider change | In the first discussion of the change I said native citations guarantee a relevant span; they only guarantee a valid pointer and exact text (the model still picks the span) | Re-reading the citation docs while writing §8.5 | Design now states precisely what is guaranteed by code and what is measured |
| 2026-11-13 | provider change | A first model list filtered out the newest family (GPT-6) because of a regex that only kept older name patterns | The models page named IDs the list lacked; re-listed without the filter | Routing is built on the GPT-6 line, which the key can call |
| 2026-11-13 | S0.15 | Test left Celery on an in-memory broker, breaking e2e tests in a single `make test` run | Full-suite run | Fixture restores broker, AMQP and result-backend caches |
| 2026-11-13 | S0.25 | The design treated OpenAI prompt caching as free upside (cheap reads). The spike's usage fields showed every prompt over 1,024 tokens billed as a 1.25× cache write, including RAG prompts nobody reuses | Reading `cache_write_tokens` in the spike output, then a probe on a fresh prompt | `cache_mode=explicit` support in the provider, design §4.4 rule, follow-up F-021 for the gateway default |
| 2026-11-13 | S0.23 | `strict_schema` stripped every key named `title`/`default`, including real properties with those names; the unit test only used fields that avoided both | Post-S0 self-review (reading the code against likely S6 schemas), confirmed with a 3-field model | Keyword stripping limited to schema nodes; `properties`/`$defs` names preserved; regression test |
| 2026-11-13 | C1 | Authors (the AI) wrote both the ingestion code and its tests, so 16 defects passed 143 tests, e.g. commit-phase errors that looped a job forever, a quadratic sentence splitter, DOCX text silently dropped in tracked changes | A second, independent AI reviewer (fresh context, read-only, told to distrust the tests) reproduced each defect against the code | All fixed with regression tests; independent review is now a checkpoint step (`CLAUDE.md`, plan README) |
| 2026-11-12 | S0.03 | Plan scripts assumed unticked boxes; ticking progress would have broken lint and been wiped by renumbering | Read before ticking | Regexes accept `[x]`; renumber preserves ticks |
| 2026-11-13 | S16.02 | Concluded from a first retrieval A/B that the reranker "hurts" recall. The reranker had been silently skipped (timeouts), so the run measured fusion order only, mislabeled as reranked | Looking at per-call timings, not just totals; native benchmark showed 10 threads/batch 64 = 1031 ms and 1.5 GB vs 4 threads/batch 8 = 288 ms and 343 MB | Thread, batch and passage-length config, focus windows, degraded-run guard that aborts evals (`eval/run.py`, S16.04); container p50 351 ms |
| 2026-11-13 | S16.05 | Blamed the AMD 10-K extraction slowness on page count and added limits | Profiling: pdfplumber `dedupe_chars` was O(n^2), 93% of time, and glyph dicts were retained | `dedupe_chars_fast`, words released per page (`ingestion/extract/pdf.py`): 11 s / 198 MB |
| 2026-11-13 | S16.08 | Concurrent brief workers read a shared group counter after an `await`, so two workers could take the same group and one group was skipped | Review of the test's output ordering; reproduced with a scripted provider | Claim `next_group` before any await (`insights/stage.py`) |
| 2026-11-13 | S16.03 | Focus window cap in words exceeded the reranker's 1200-character limit (1424 chars) | Container returned truncation errors in the first bench | `max_chars` in `focus_window` (`retrieval/windows.py`) |
| 2026-11-13 | S16 | Committed with lint failing (long lines, S608) because I checked a pipeline's last command, not the `make` exit code | Next CI run failed | Always read the `make` exit code directly |
| 2026-11-13 | docs | A broad "superseded" rewrite of old plan files hid tasks that were already built (S14.06 AC1-3, S11.03 AC1-7) | Re-reading ticked vs unticked counts after the rewrite | Restored and ticked from evidence |
| 2026-11-13 | tests | Ran two pytest invocations against the shared test DB at once; they appeared to hang | Locks on the shared DB; reproduced alone | Rule in HANDOFF/CLAUDE.md: never overlap test runs |

## Prompt iteration log
| Date | Prompt id@version | Change | Eval result | Kept? |
|---|---|---|---|---|
| 2026-11-13 | analyze_query@1 | Initial: untrusted-data section, 4 few-shot examples (follow-up, compare, clarify, injection), refs instead of ids | 8/8 representative questions correct live ($0.0007); full eval delta pending S3.15 | yes |
| 2026-11-13 | answer_rag@1 | Initial: 7-part skeleton (ground rules, citation rule, route format, coverage, conflicts, length, history), handles only, abstain rule | not evaluated yet (S3.15 baseline) | yes |
| 2026-11-13 | brief_extract@1 | Initial: 7-section skeleton (ground rules, untrusted-content rule, field guide, summary/question/category guidance), facts with evidence handles only, role balanced/effort low | live, 3 corpus documents: 56/58 facts verified (97%), category correct 3/3, $0.043 total (`BRIEF_SAMPLES_OUT=docs/evidence/brief_samples.json uv run pytest tests/live/insights/test_brief_live.py -m live`) | yes |
| 2026-11-13 | brief_merge@1 | Initial: merges map-step document-level fields (summary, category, tags, questions); facts are deduplicated in code and keep their citations | unit-tested; not exercised live (all 3 sample documents fit one call) | yes |
