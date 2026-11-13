"""HTTP latency benchmark against a running reranker container (S16.02).

    python3 bench_http.py --n 20 --requests 20 [--url http://localhost:8180] [--words 180]

Standard library only. Prints p50/p95 ms and the container's `docker stats` line.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
import urllib.request

WORDS = (
    "the quarterly revenue increased because customers renewed their annual contracts while "
    "operating costs remained flat across regions and the board approved a new policy"
).split()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8180")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--requests", type=int, default=20)
    ap.add_argument("--words", type=int, default=180)
    a = ap.parse_args()
    passages = [" ".join(WORDS[(i + j) % len(WORDS)] for j in range(a.words)) for i in range(a.n)]
    body = json.dumps({"query": "how did revenue change", "passages": passages}).encode()
    lat = []
    for i in range(a.requests + 2):
        t = time.perf_counter()
        req = urllib.request.Request(a.url + "/rerank", body, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        if i >= 2:  # two warmups
            lat.append((time.perf_counter() - t) * 1000)
    lat.sort()
    print(json.dumps({"requests": a.requests, "passages": a.n, "p50_ms": round(statistics.median(lat), 1),
                      "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1)}))
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}} {{.CPUPerc}}",
                          "vault-reranker-1"], capture_output=True, text=True)
    print(out.stdout.strip() or "docker stats unavailable")


if __name__ == "__main__":
    main()
