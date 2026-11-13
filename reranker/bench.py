"""Native latency + memory benchmark for the reranker (S16.02).

    uv run python bench.py --threads 4 --batch 8 --words 180 --n 40 --repeats 3

Prints one JSON line with p50/p95 ms and peak RSS. Baseline measured 2026-11-13 on an idle M4 (docs/plan/v3.md
section 6): 40 passages x ~243 tokens, 4 threads, batch 8 -> 288 ms, 343 MB; the old defaults (10 threads,
batch 64, ~513 tokens) -> 1031 ms, 1.5 GB. Run on an idle machine (load < 4).
"""

from __future__ import annotations

import argparse
import json
import resource
import statistics
import sys
import time

from app import Config, load_scorer
from models import resolve_model

WORDS = (
    "the quarterly revenue increased because customers renewed their annual contracts while "
    "operating costs remained flat across regions and the board approved a new policy"
).split()


def passage(i: int, n_words: int) -> str:
    return " ".join(WORDS[(i + j) % len(WORDS)] for j in range(n_words))


def peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1e6 if sys.platform == "darwin" else 1e3), 1)  # bytes on macOS, KiB on Linux


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="minilm")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--words", type=int, default=180)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--repeats", type=int, default=10)
    a = ap.parse_args()
    spec = resolve_model(a.model)
    cfg = Config(model=spec.hf_id, threads=a.threads, batch_size=a.batch)
    score = load_scorer(spec, cfg)
    query = "how did revenue change and what did the board decide"
    passages = [passage(i, a.words) for i in range(a.n)]
    for _ in range(2):  # warmup
        score(query, passages)
    lat = []
    for _ in range(a.repeats):
        t = time.perf_counter()
        score(query, passages)
        lat.append((time.perf_counter() - t) * 1000)
    lat.sort()
    print(
        json.dumps(
            {
                "model": spec.hf_id,
                "threads": a.threads,
                "batch": a.batch,
                "words": a.words,
                "passages": a.n,
                "p50_ms": round(statistics.median(lat), 1),
                "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1),
                "peak_rss_mb": peak_rss_mb(),
            }
        )
    )


if __name__ == "__main__":
    main()
