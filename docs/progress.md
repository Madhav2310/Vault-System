# Progress log

One entry per task, newest last. A task's checkbox in `docs/plan/` is ticked only when its entry below shows evidence. Shortfalls and postponed work go to `docs/followups.md`.

## Entry template
```
### <task ID> — <title>
- Date: YYYY-MM-DD · Commit: <sha>
- Commands: <commands run>
- Output: <real output summary>
- Status: VERIFIED | UNVERIFIED (<what and why>)
- [T] changes approved: none | <metric: old → new, approved by user on date>
```

## Threshold changes
| Date | Task | Metric | Old | New | Approved by |
|---|---|---|---|---|---|

---

### S0.01 — Repository init
- Date: 2026-11-12 · Commit: 2751c85
- Commands: `git init -b main`; `git rev-parse --abbrev-ref HEAD`; `git ls-files docs/archive | wc -l`; `git ls-files | grep -c DS_Store`; `git show --stat HEAD`
- Output: branch `main`; 0 archive files tracked; 0 `.DS_Store`; first commit lists `CLAUDE.md`, `docs/design.md`, `docs/plan/*`, `docs/research/*`, `docs/spec/case-study.pdf` (43 files).
- Status: VERIFIED
- [T] changes approved: none

### S0.02 — Python project and tooling
- Date: 2026-11-12 · Commit: d1219bb
- Commands: `cd backend && uv sync --frozen`; `make lint`; `make typecheck`; `make -n <each target>`; `make ci`
- Output: sync exit 0 (CPython 3.12.12, uv-managed, 39 packages); lint exit 0; mypy strict "no issues found in 2 source files"; all 17 targets defined (unimplemented ones print the owning task and exit 1); `make ci` exit 0, `tests/unit/test_smoke.py::test_import_app` passed.
- Status: VERIFIED
- Incident: the first `uv lock` probed Homebrew `python@3.13` during interpreter discovery and macOS killed it (SIGKILL, Code Signature Invalid, while mapping `lib-dynload/binascii.cpython-313-darwin.so`; reports `~/Library/Logs/DiagnosticReports/Python-2026-11-12-2249*.ips`, `-225045.ips`). Fix: `[tool.uv] python-preference = "only-managed"` + `backend/.python-version` = 3.12, so uv never launches Homebrew/system interpreters. No crash since. See followups F-001.
- [T] changes approved: none

### S0.03 — Progress and AI-usage logs
- Date: 2026-11-12 · Commit: (this commit)
- Commands: S0.03 AC1 and AC2 grep commands (threshold-table heading count; AI_USAGE `## ` section count)
- Output: 1; 5
- Status: VERIFIED
- Also: `scripts/plan_lint.py` and `scripts/plan_renumber.py` now accept/preserve ticked `[x]` boxes (they assumed `[ ]` only).
- [T] changes approved: none
