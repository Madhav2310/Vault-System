# app.llm

The only path to a model (design §4, §5.3).

- `gateway.py`: every call goes through it (routing, turn budget, retries with jitter, one `llm_calls` row per attempt, schema validation, fallback)
- `routing.py`: model roles (`fast`/`balanced`/`deep`/`flagship`) and per-node routing; the only module besides settings that names models
- `pricing.py`: per-model prices and the cost formula
- `prompts.py`: registry of `backend/prompts/<id>@<version>.md` files
- `types.py`, `errors.py`: provider-neutral request/response types and typed errors
- `providers/`: OpenAI chat (Responses API) and embeddings adapters, a dormant Anthropic adapter, and a scripted fake for tests
- `fake.py`: scripted model for the UI test harness; test-only, refused in production

Invariants: provider clients are constructed only in `providers/`; the LLM never picks its own model; prompt changes bump the version.
