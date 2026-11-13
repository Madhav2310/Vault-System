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
| 2026-11-12 | S0.03 | Plan scripts assumed unticked boxes; ticking progress would have broken lint and been wiped by renumbering | Read before ticking | Regexes accept `[x]`; renumber preserves ticks |

## Prompt iteration log
| Date | Prompt id@version | Change | Eval result | Kept? |
|---|---|---|---|---|
