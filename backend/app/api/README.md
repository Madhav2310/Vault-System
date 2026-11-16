# app.api

HTTP layer. Routers validate, authorize and shape responses; simple reads run owner-scoped SQL directly, and domain work lives in app/documents, app/chat and app/insights.

- `documents.py`, `versions.py`, `chunks.py`: documents, upload, processing timeline/stream, original file, chunk inspection
- `chat.py`, `runs.py`, `feedback.py`: sessions and message posting (SSE), "why this answer", ratings
- `metrics.py`, `health.py`, `session.py`: statistics (`?scope=global` is admin only), health, API-key to cookie exchange
- `schemas.py`: response models used for OpenAPI and response validation; `openapi.py`: tags, security scheme, error model
- `errors.py`: `application/problem+json` with `code` and `retryable` (codes from `core/error_codes.py`)
- `idempotency.py`: `Idempotency-Key` for resource-creating POSTs; `middleware.py`: request ID, bound log context, access log
- `deps.py`: shared dependencies (auth, sessions)

Invariants: every query is owner-scoped and another user's resource returns 404; POST handlers are idempotent
(at-least-once delivery); no document text in logs.
