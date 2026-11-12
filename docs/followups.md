# Follow-ups and known shortfalls

Running list kept during execution. Two kinds of entry:
- **Shortfall**: something done in a weaker form than planned (why, impact, what would fix it).
- **Deferred**: an extra task that came up during building and is postponed (why it matters, when to do it).

Each entry: `ID · date · task · kind` — description. Resolved entries are struck through with the resolving commit/task.

| ID | Date | Task | Kind | Description | Status |
|---|---|---|---|---|---|
| F-001 | 2026-11-12 | S0.02 | Shortfall (env) | Homebrew `python@3.13` crashes (SIGKILL, invalid code signature) when uv probes it. Worked around with uv `only-managed`; the Homebrew install itself is untouched. **User option:** `brew reinstall python@3.13` (or uninstall if unused). Any tool that scans PATH interpreters may still trigger the crash dialog. | open (user) |
| F-002 | 2026-11-12 | S0.01 | Deferred | Root `.venv` (Python 3.14, used by `eval/build/*` during planning) is outside the uv project. S0.18 should move eval-build deps into the uv project (`dependency-groups.eval`) and delete the root `.venv`. | open → S0.18 |
| F-003 | 2026-11-12 | S0.04 | Shortfall | MinIO no longer publishes images on Docker Hub (`minio/minio` repo gone; quay tags not found). The `minio` profile runs SeaweedFS 4.50 (S3 API). If S0.12 [OPT] is done, rename the profile to `s3` and update the plan/design wording. | open |
| F-004 | 2026-11-12 | S0.04/06/07 | Shortfall (process) | Some commits bundle two tasks (S0.06+S0.07 share wiring; compose files landed inside the S0.06/07 and S0.08 commits via `git add -A`). History is still traceable via progress.md. From now on: stage paths explicitly per task. | mitigated |
| F-005 | 2026-11-12 | S0.09 | Deferred | The dev API keys for alice/bob were printed once and redacted, so nobody has them. Add `python -m app.db.seed rotate-keys` before the demo or frontend work (S9/S10). | open |
