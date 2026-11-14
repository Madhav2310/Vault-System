# app.chat

Chat turn engine (design §8). `turn.py` wires the explicit graph (`graph.py`: typed state, async nodes, routers) over `nodes/`.

- `nodes/`: one module per graph node (load_context, analyze, retrieve, gate, generate, verify, persist, terminal, ...)
- `routing_rules.py`, `locate.py`, `full_context.py`, `compare.py`, `meta.py`, `clarify.py`: route decisions and special routes
- `evidence.py`, `citations.py`, `segments_reader.py`: handles (`E3.1`), strict answer schema, code-side citation resolution
- `gate.py`, `abstain.py`, `coverage.py`, `support_check.py`, `conflicts.py`, `calc.py`: evidence gate, scored abstention, verification
- `events.py`, `stream.py`: typed turn events and SSE framing
- `sessions.py`, `history.py`, `memory.py`, `ledger.py`, `reaper.py`: sessions and dedupe, read model, working memory, evidence ledger persistence, run reaper
- `harness.py`, `speculative.py`, `voice.py`, `followups.py`, `nodes_common.py`: budget, latency-only prefetch, prompt voice, follow-ups, shared helpers

Invariants: code, not the model, picks control flow and validates citations (unknown handle is dropped); answers are never cached;
no DB connection is held across an LLM call or stream.
