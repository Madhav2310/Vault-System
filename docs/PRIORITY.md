# Remaining work in priority order (2026-11-13)

Ordering rule: what a reviewer sees and grades first (runnable demo, UI, cited answers, README, AI usage), then quality evidence, then depth features, then optional. Each tier is worth finishing before the next. Status: **running** = a lane is working now; **ready** = unblocked; **needs quiet machine** = measurement run.

## Tier 1: the demo works end to end (do first)
| # | Task | Why it ranks here | Status |
|---|---|---|---|
| 1 | S19.02 fake-model UI harness, `make ui-e2e` | Free, deterministic UI tests; unblocks every UI tick | running (E) |
| 2 | S19.05 chat UI: streaming, hover/click citations, trust states, why panel | The core product experience; graded UX | running (P) |
| 3 | S19.04 finish: run its e2e specs; S19.03 e2e specs | Ticks 9 ACs on evidence | after #1 |
| 4 | S17.09 support check (faithfulness per segment) | Strongest trust feature, distinguishes from plain RAG | running (J) |
| 5 | S16.07 gate: `make ci && make test`, STATUS refresh, AMD time table | Proves nothing regressed after 8 merges | after lanes quiet |
| 6 | S20.01 README final + S20.06 demo script (`scripts/demo.sh`) + recording | Reviewers run it first; docs are missed in most submissions | ready after #2 |
| 7 | S20.05 AI_USAGE.md final (8 headings, prompt log) | One of three grading thirds | started; finish last |

## Tier 2: evidence the answers are good
| # | Task | Why | Status |
|---|---|---|---|
| 8 | S17.02 retrieval re-baseline with the working reranker | First true measurement; decides config | needs quiet machine |
| 9 | S17.13 gate: answer-quality baseline (`make eval-answers`, spend cap $3) | The numbers for README and the write-up | after #8 |
| 10 | S20.03 evaluation write-up, S20.04 scale doc, S20.02 diagrams | Answers "how does it scale" and shows rigor | running (O) |
| 11 | S17.06 `retrieval@2` final config + refreeze | Locks the numbers in #9 | after #8 |
| 12 | S17.07 whole-document answering | Big quality win on small scopes; A/B decides default | running (K) |
| 13 | S17.10 fast path and per-node timings | Latency (5.9 s p50 today) | ready |

## Tier 3: product depth
| # | Task | Status |
|---|---|---|
| 14 | S18.05 meta route ("how many documents do I have?" via SQL) | ready |
| 15 | S18.09 stale answers and re-ask | ready |
| 16 | S18.06 compare across documents, S18.07 conflicting sources | after #12 |
| 17 | S19.06 accessibility (axe) pass | after #3 |
| 18 | S17.08 evidence gate (no-LLM floor) | ready (code was started and stopped) |

## Tier 4: only if time and budget remain (cut first)
| Task | Note |
|---|---|
| S17.03 LLM listwise rerank | Needs a measured win over the cross-encoder; can ship as "tried, not adopted" |
| S17.05 contextual chunk headers | Index-side only; costs per document; measure on a sample |
| S20.07 judge calibration with human labels | Needs your 40 labels; optional |
| S18.02 `--stream` eval flag | Small |

## Final gates (in order)
S18.12 gate, S19.07 gate (you try the UI), S20.08 fresh-clone check and final review, then spend report (cap $30; about $3 spent so far).

## Needs you
Approve gates; record the demo video; try the UI; optional labels.
