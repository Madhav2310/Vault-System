# Follow-ups and known shortfalls

Running list kept during execution. Two kinds of entry:
- **Shortfall**: something done in a weaker form than planned (why, impact, what would fix it).
- **Deferred**: an extra task that came up during building and is postponed (why it matters, when to do it).

Each entry: `ID · date · task · kind` — description. Resolved entries are struck through with the resolving commit/task.

| ID | Date | Task | Kind | Description | Status |
|---|---|---|---|---|---|
| F-001 | 2026-11-12 | S0.02 | Shortfall (env) | Homebrew `python@3.13` crashes (SIGKILL, invalid code signature) when uv probes it. Worked around with uv `only-managed`; the Homebrew install itself is untouched. **User option:** `brew reinstall python@3.13` (or uninstall if unused). Any tool that scans PATH interpreters may still trigger the crash dialog. | open (user) |
| F-002 | 2026-11-12 | S0.01 | Deferred | Root `.venv` (Python 3.14, used by `eval/build/*` during planning) is outside the uv project. S0.18 should move eval-build deps into the uv project (`dependency-groups.eval`) and delete the root `.venv`. | open → S0.18 |
