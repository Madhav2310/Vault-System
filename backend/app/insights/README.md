# app.insights

Document-level AI outputs (design §9).

- `brief.py`: DocumentBrief model-output schema, code-side normalization, stored form
- `stage.py`: the brief stage, the last ingestion stage, run on the `ai` queue
- `verify.py`: fact verification against stored evidence; pure functions, no model, no I/O
- `staleness.py`: hook called once when a version becomes `ready`, in the same transaction
- `views.py`: read side of the brief (`GET /v1/documents/{id}/brief` and the list snippet)
- `summaries.py`: custom summaries (`POST /v1/documents/{id}/summaries`), also on the `ai` queue

Invariants: facts must verify against stored `original_text`; stages are idempotent; model calls go through `app.llm.gateway`.
