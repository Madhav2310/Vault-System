# scripts

| Script | Purpose | Used by |
|---|---|---|
| `quickstart.sh` | One-command start: checks Docker, creates `.env`, starts the stack, migrates, creates a demo user and prints its API key | README quickstart |
| `demo.sh` | Full curl demo: upload, status, guides, streamed chat, compare, why panel, stale marker, metrics, spend | README, `docs/operations.md` section 7 |
| `demo_core.sh` | Shorter smoke demo: upload, ready, cited answer, history, metrics | README |
| `fresh_clone_check.sh` | Clones the repo to a temp dir and runs the reviewer path (prerequisites, `.env`, `make ci`); `--stack` adds `make up`, `/readyz`, the core demo smoke and `make down`; prints a pass/fail table | release check, `docs/operations.md` section 1 |
| `wait_healthy.sh` | Waits until compose services are healthy | Makefile `up*` targets |
| `check_trace.py` | Sends a request with a known trace id and finds it in Tempo (needs the observability stack) | manual check of trace propagation |
| `export_postman.py` | Generates `docs/api/vault.postman_collection.json` from the OpenAPI schema | `backend/tests/unit/api/test_postman_export.py` |
| `gen_turn_diagram.py` | Writes `docs/img/chat_turn.mmd` from the turn graph | `make diagrams`, `backend/tests/unit/docs/test_diagrams.py` |
