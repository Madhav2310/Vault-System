"""S16.02: truncation and backpressure with the new defaults."""

from __future__ import annotations

import threading
import time

from test_api import fake_client


def test_long_passage_truncated() -> None:
    seen: list[list[str]] = []

    def scorer(_q: str, ps: list[str]) -> list[float]:
        seen.append(ps)
        return [1.0] * len(ps)

    with fake_client(scorer, max_passage_chars=1200) as c:
        long_text = "word " * 1000  # 5000 chars
        r = c.post("/rerank", json={"query": "q", "passages": [long_text, "short"]})
        assert r.status_code == 200 and len(r.json()["scores"]) == 2
    assert len(seen[0][0]) <= 1200 and not seen[0][0].endswith("wor") and seen[0][1] == "short"


def test_overloaded_503() -> None:
    gate = threading.Event()

    def slow(_q: str, ps: list[str]) -> list[float]:
        gate.wait(5)
        return [0.0] * len(ps)

    with fake_client(slow, max_in_flight=1, queue_timeout_ms=200) as c:
        codes: list[int] = []

        def call() -> None:
            codes.append(c.post("/rerank", json={"query": "q", "passages": ["a"]}).status_code)

        ts = [threading.Thread(target=call) for _ in range(2)]
        for t in ts:
            t.start()
        time.sleep(0.8)
        assert codes == [503]
        gate.set()
        for t in ts:
            t.join()
        assert sorted(codes) == [200, 503]
