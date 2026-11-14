# scripts

| Script | Purpose | Used by |
|---|---|---|
| `quickstart.sh` | One-command start: checks Docker, creates `.env`, starts the stack, migrates, creates a demo user | README quickstart |
| `demo.sh` | Full curl demo: upload, status, guides, streamed chat, compare, why panel, stale marker, metrics, spend | README, `docs/ops/demo.md` |
| `demo_core.sh` | Shorter smoke demo (upload, ready, cited answer, history, metrics) | README, plan S16 |
| `fresh_clone_check.sh` | Clones HEAD to a temp dir, brings the stack up under a separate compose project, waits healthy | plan S20.08 |
| `wait_healthy.sh` | Waits until compose services are healthy | Makefile `up*` targets |
| `check_trace.py` | Sends a request with a known trace id and finds it in Tempo | plan S0.15 (needs the obs stack) |
| `export_postman.py` | Generates `docs/api/vault.postman_collection.json` from the OpenAPI schema | `test_postman_export.py` |
| `gen_turn_diagram.py` | Writes `docs/img/chat_turn.mmd` from the turn graph | `make diagrams`, `test_diagrams.py` |
| `plan_lint.py` | Consistency checks for `docs/plan/` (exit 1 on error) | CLAUDE.md, plan workflow |
| `plan_tick.py` | Ticks acceptance criteria in `docs/plan/` | `docs/plan/v3.md` |
| `plan_core_closure.py` | Checks the core-path dependency closure of the plan | plan README (manual) |
| `plan_renumber.py` | Renumbers plan tasks and rewrites references | manual, plan editing |
| `spikes/` | One-off spike scripts (citations, geometry, schema); outputs in `docs/evidence/` | evidence docs |

Nothing here is obsolete: every script is referenced by the Makefile, README, a test or the plan docs, so none was archived.
