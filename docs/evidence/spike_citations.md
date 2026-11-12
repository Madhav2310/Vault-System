# Spike S0.25: application-managed citations on OpenAI (2026-11-13)

Regenerate: `cd backend && uv run --frozen python ../scripts/spikes/citations_spike.py --run --max-usd 0.50`, then `--check <name>`. Raw per-item results (handles, outputs, usage, recorded stream deltas): `docs/evidence/spike_citations_raw.json`. Selection is deterministic: the first 12 smoke-split items (sorted by id) in the categories lookup, identifier, injection, conflict, compare and unanswerable; seed 20261113.

## Setup
- `balanced` role (`gpt-6.1-terra`), reasoning effort `none`, through the gateway (router row `generate:qa`, mode `rag`). FULL_CONTEXT mode uses the `generate::full_context` row (effort `low` in the table; the spike passed mode `full_context`).
- Evidence set per item: the window of 6 sentences holding the gold sentence plus the next window, then 2 to 4 distractor windows (borrowed from an unrelated contract for the tiny authored documents), shuffled. Handles `E<k>.<i>`; the schema enum lists exactly the handles in the request.
- Sentence splitter and extraction are throwaway (regex splitter; pdfplumber with `use_text_flow=True, x_tolerance_ratio=0.15`). The real chunker is S1.

## Results
| Check | Result | Target |
|---|---|---|
| `enum_validity` (12 RAG items) | 0 invalid handles | 0 [A] |
| `segment_support` | 15 of 15 segments (100%) carry ≥1 valid handle, over 9 answered items | ≥ 90% [T] |
| `gold_cited` (lookup, identifier, injection items) | 5 of 5 cite the gold sentence (100%) | ≥ 80% [T] |
| `streaming` | 2 of 2 streams parse incrementally: first segment closes at delta 23 of 96 and 40 of 81; segments equal the non-streamed parse | pass |
| `full_context_validity` (3 items, plain-string handles, 126 to 177 sentences) | 0 of 4 handles invalid; every returned handle resolves | ≤ 5% [T] |
| `cost` | $0.054 total for 17 calls | < $0.50 cap |

Other observations:
- The 3 unanswerable items all returned `status=abstain` with no segments (3 of 3).
- The two conflict items each produced one segment citing both sources (`E1.5`+`E2.3`, `E1.3`+`E4.2`), matching the gold sentences exactly.
- The franchise anti-assignment answer cited the gold sentence plus its neighbour (`E2.4`, `E2.5`); a compare item cited 5 of its 6 gold sentences.
- Sync latency on RAG prompts of 5 to 20K characters: 1.8 to 3.3 s total, 17 to 103 output tokens. Streaming: first delta at 1.25 s and 2.15 s, completed at 2.13 s and 3.19 s.
- Per-call cost: $0.0012 to $0.0070 (RAG), $0.0007 to $0.0124 (FULL_CONTEXT, 6.7K to 9.8K prompt tokens).

## Caveats (hostile-reader notes)
- **Small samples** (F-022): 5 lookup-type items and 4 FULL_CONTEXT handles do not prove the targets. They show the mechanism works; S3.16 and the S2 eval measure it properly.
- **Enum validity is by construction**: constrained decoding makes an invalid handle in RAG mode impossible, so the 0 is expected. The meaningful RAG numbers are segment support and gold-cited.
- **Support is not checked here**: citing a gold sentence does not prove the sentence supports the claim; that is the faithfulness metric in the eval.
- The streaming reader in the script is a prototype for S3.07, tested on only 2 recorded streams.

## Finding: cache writes are billed by default (F-021)
In the spike, nearly all RAG input tokens were billed as cache writes (e.g. `input_tokens=3`, `cache_write_tokens=9800`), at 1.25× the input rate, although no request reused them. A probe with a fresh 4.5K-token prompt on `gpt-6-luna`:

| `prompt_cache_options.mode` | uncached input | cache write |
|---|---|---|
| unset (implicit) | 3 | 4,513 |
| `explicit`, no breakpoints | 4,518 and 4,516 | 0 and 0 |

So RAG calls should run with `explicit` and no breakpoints, and FULL_CONTEXT calls should mark one breakpoint after the document block (design §4.4). The provider and `ChatRequest.cache_mode` already support the setting.
