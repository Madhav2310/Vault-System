from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app as app_mod
from app import Config, Scorer, create_app

QUERY = "What is the capital of France?"
RELEVANT = "Paris is the capital and most populous city of France."
IRRELEVANT = "Photosynthesis converts light energy into chemical energy in plants."


@pytest.fixture(scope="module")
def real_client() -> Iterator[TestClient]:
    # Real MiniLM; needs the model cached (first run downloads it).
    with TestClient(create_app(Config(model="minilm"))) as c:
        yield c


def fake_client(scorer: Scorer, **kw: Any) -> TestClient:
    return TestClient(create_app(Config(**kw), scorer=scorer))


def test_scores_shape(real_client: TestClient) -> None:
    for n in (0, 1, 7):
        r = real_client.post("/rerank", json={"query": QUERY, "passages": [RELEVANT] * n})
        assert r.status_code == 200
        assert len(r.json()["scores"]) == n
    assert real_client.get("/healthz").json()["status"] == "ok"


def test_relevance_order(real_client: TestClient) -> None:
    r = real_client.post("/rerank", json={"query": QUERY, "passages": [IRRELEVANT, RELEVANT]})
    s = r.json()["scores"]
    assert s[1] > s[0]


def test_backpressure_503() -> None:
    gate = threading.Event()

    def slow(_q: str, ps: list[str]) -> list[float]:
        gate.wait(5)
        return [0.0] * len(ps)

    with fake_client(slow, max_in_flight=2, queue_timeout_ms=300) as c:
        results: list[tuple[int, float, dict[str, object]]] = []

        def call() -> None:
            t = time.monotonic()
            r = c.post("/rerank", json={"query": "q", "passages": ["a"]})
            results.append((r.status_code, time.monotonic() - t, r.json()))

        threads = [threading.Thread(target=call) for _ in range(2 + 3)]
        for t in threads:
            t.start()
        time.sleep(1.0)  # excess requests must be rejected while the workers are still busy
        rejected = [x for x in results if x[0] == 503]
        assert len(rejected) == 3
        for _code, elapsed, body in rejected:
            assert body == {"code": "overloaded", "retryable": True}
            assert elapsed < 0.8
        gate.set()
        for t in threads:
            t.join()
        assert sorted(x[0] for x in results) == [200, 200, 503, 503, 503]
        # slots are released afterwards
        assert c.post("/rerank", json={"query": "q", "passages": ["a"]}).status_code == 200


def test_request_limits_422() -> None:
    with fake_client(lambda q, ps: [0.0] * len(ps), max_passages=3, max_passage_chars=10) as c:
        assert c.post("/rerank", json={"query": "q", "passages": ["a"] * 4}).status_code == 422
        assert c.post("/rerank", json={"query": "q", "passages": ["x" * 11]}).status_code == 422
        assert c.post("/rerank", json={"query": "", "passages": ["a"]}).status_code == 422
        assert c.post("/rerank", json={"query": "q", "passages": ["a"] * 3}).status_code == 200


def test_spans_have_no_text() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app_mod.tracer = provider.get_tracer("test")
    try:
        with fake_client(lambda q, ps: [0.0] * len(ps)) as c:
            c.post("/rerank", json={"query": "SECRET-QUERY", "passages": ["SECRET-PASSAGE"]})
    finally:
        app_mod.tracer = trace.get_tracer("vault.reranker")
    spans = exporter.get_finished_spans()
    assert [s.name for s in spans] == ["rerank"]
    attrs = dict(spans[0].attributes or {})
    assert attrs["rerank.passages"] == 1
    assert "SECRET" not in repr(attrs)
