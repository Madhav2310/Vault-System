# app.core

Cross-cutting infrastructure with no domain logic.

- `settings.py`: typed configuration; every value is listed in `.env.example`; keys only come from `.env`
- `auth.py`: API-key authentication (only the SHA-256 of a key is stored)
- `ratelimit.py`: per-user Redis token bucket; `quota.py`: daily LLM cost quota per user
- `ids.py`: UUIDv7 with a counter for monotonic ordering
- `logging.py`: structured JSON logging
- `error_codes.py`: the central error catalog every API error draws from

Invariant: logs carry IDs and digests only, never document text, prompts or completions.
