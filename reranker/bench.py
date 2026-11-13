"""Latency benchmark: 40 pairs x ~400 tokens. Usage: python bench.py --model minilm|bge."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time

from app import Config, load_scorer
from models import resolve_model

WORDS = (
    "the quarterly revenue increased because customers renewed their annual contracts while "
    "operating costs remained flat across regions and the board approved a new policy"
).split()


def passage(i: int, n_words: int) -> str:
    return " ".join(WORDS[(i + j) % len(WORDS)] for j in range(n_words))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="minilm")
    ap.add_argument("--pairs", type=int, default=40)
    ap.add_argument("--tokens", type=int, default=400)
    ap.add_argument("--iters", type=int, default=30)
    a = ap.parse_args()
    spec = resolve_model(a.model)
    cfg = Config(model=spec.hf_id)
    score = load_scorer(spec, cfg)
    query = "how did revenue change and what did the board decide"
    # Plain lowercase words are ~1 wordpiece each; the exact count is measured and printed.
    passages = [passage(i, a.tokens) for i in range(a.pairs)]
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    enc = TextCrossEncoder(spec.hf_id, cache_dir=cfg.cache_dir, lazy_load=True)
    tokens = enc.token_count([(query, passages[0])])
    for _ in range(3):  # warmup
        score(query, passages)
    lat = []
    for _ in range(a.iters):
        t = time.perf_counter()
        score(query, passages)
        lat.append((time.perf_counter() - t) * 1000)
    lat.sort()
    p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
    print(
        json.dumps(
            {
                "model": spec.hf_id,
                "pairs": a.pairs,
                "tokens_per_pair": tokens,
                "threads": cfg.max_in_flight,
                "cpus": os.cpu_count(),
                "iters": a.iters,
                "p50_ms": round(statistics.median(lat), 1),
                "p95_ms": round(p95, 1),
            }
        )
    )


if __name__ == "__main__":
    main()
