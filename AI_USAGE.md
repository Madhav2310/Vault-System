# AI usage

How AI tools were used to design and build this project, including where they were wrong and how that was caught.

## Tools used
- **Claude Code (Claude Opus 5.5)**: research, design, planning, implementation, tests, eval-set construction.
- **ChatGPT**: independent critique of the design. Its review was reconciled into design v2 (decision log D1–D25 in `docs/design.md`).
- **Claude / OpenAI / DeepSeek APIs**: product runtime (answers, analysis, embeddings) and cross-family eval judges.

## Workflow
1. **Design first.** Design (`docs/design.md`) and research passes (`docs/research/01–06`) were written before any code, then cross-reviewed with ChatGPT.
2. **Plan with checks.** `docs/plan/` holds 190 tasks and 756 acceptance criteria. Each criterion is verified by a named test, a command or a manual check. `scripts/plan_lint.py` checks the plan's consistency.
3. **Build one task at a time.** Tests come first, then one commit per task prefixed with the task ID. Evidence goes in `docs/progress.md`, and shortfalls and postponed work go in `docs/followups.md`.
4. **Claude Code rules.** Project rules in `CLAUDE.md` constrain Claude Code: the gateway-only LLM calls, the citation/format exclusion, owner scoping, no content in logs, and the rest.

## Per-slice log
### S0
- S0.01–S0.03: repo init, uv/ruff/mypy/pytest skeleton, logs.

## Corrections
| Date | Task | What the AI got wrong | How it was caught | Fix |
|---|---|---|---|---|
| 2026-11-12 | planning | Renumber tool rewrote range endpoints twice (12 ranges corrupted) | `plan_lint` + manual review | Single-pass regex; ranges repaired by meaning |
| 2026-11-12 | planning | Default pdfplumber extraction merged words and interleaved columns (QASPER quotes unlocatable) | Gold-quote locate rate 86/100 | `use_text_flow=True, x_tolerance_ratio=0.15` → 99/100 |
| 2026-11-12 | S0.02 | `uv lock` was allowed to probe Homebrew interpreters; a broken `python@3.13` crashed (user saw the crash dialog) | User report + crash log RCA | `python-preference = "only-managed"` |
| 2026-11-12 | S0.03 | Plan scripts assumed unticked boxes; ticking progress would have broken lint and been wiped by renumbering | Read before ticking | Regexes accept `[x]`; renumber preserves ticks |

## Prompt iteration log
| Date | Prompt id@version | Change | Eval result | Kept? |
|---|---|---|---|---|
