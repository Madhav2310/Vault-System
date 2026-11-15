# app.retrieval

Scoped candidate retrieval. Entry point: `retrieve()` in `retrieve.py` (scope + query -> ranked candidates with provenance).

- `scope.py`: the live document versions one owner's query may touch (the LLM never widens scope)
- `query_embed.py`: query embedding with Redis cache and lexical fallback
- `vector.py`, `lexical.py`: exact vector search and `tsv_en` lexical search
- `fusion.py`: reciprocal rank fusion; `rerank.py`: reranker service client; `listwise.py`: LLM listwise rerank; `windows.py`: query-focused windows
- `expand.py`: neighbor expansion; `load.py`: loads `EvidenceChunk`s (stored text, never altered)
- `config.py`: strict loader for `backend/config/retrieval@*.yaml` (unknown keys rejected)

Invariants: config versions are pinned and part of cache keys; retrieval degrades to fusion order if the reranker is down.
