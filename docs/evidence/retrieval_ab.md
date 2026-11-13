# Retrieval A/B log (plan S11)

Rows are appended per experiment. Regenerate with the command in the last column (backend stack up: `make up`).

| date | split | config | recall@8 | mrr@40 | wall_s | cost | command |
|---|---|---|---:|---:|---:|---:|---|
| 2026-11-13 | all (100 items with gold) | retrieval@1 (MiniLM rerank, AND lexical) | 0.744 | 0.508 | 145 (warm) | ~$0.0001 | `make eval-retrieval-fast` |
