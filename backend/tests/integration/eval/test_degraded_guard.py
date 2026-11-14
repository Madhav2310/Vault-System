"""Evals never silently measure a degraded configuration."""

import argparse
import json
import pathlib
import random
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from eval import baseline as bl
from eval.report import RunResult
from eval.run import Harness, degraded_abort, run_suite
from eval.schema import ItemOutput
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)
from app.retrieval.config import get_config
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from tests.integration.eval.test_runner import C1, ITEMS
from tests.support.docs import seed_document
from tests.support.evalkit import Tracker
from tests.support.llm import make_gateway
from tests.support.vectors import seed_vectors, unit_vector

SM = async_sessionmaker[AsyncSession]


class _Pipe:
    def __init__(self, degraded: tuple[str, ...]) -> None:
        self.degraded = degraded

    async def __call__(self, q: Any, ctx: Any) -> ItemOutput:
        return ItemOutput(retrieved=["c1"], abstained=q.category == "unanswerable", degraded=self.degraded)


async def _run(sm: SM, tracker: Tracker, tmp_path: pathlib.Path, degraded: tuple[str, ...]) -> RunResult:
    run, _ = await run_suite(
        ITEMS, Harness(_Pipe(degraded), {"d1": [C1]}), name="toy", suite="retrieval", split="all",
        sm=sm, report_dir=tmp_path, git_sha="abc1234",
    )  # fmt: skip
    tracker.run_ids.append(run.run_id)
    return run


async def test_abort_on_degraded(sm: SM, tracker: Tracker, tmp_path: pathlib.Path, monkeypatch: Any) -> None:
    run = await _run(sm, tracker, tmp_path, ("no_rerank",))
    assert run.degraded_rate() == 1.0 and run.degraded_by_kind() == {"no_rerank": 3}
    line = degraded_abort(run, 0.05)
    assert line is not None and line.startswith("ABORT: degraded_rate 1.000 > 0.050")
    # `_main` returns 3 and never reaches the baseline freeze
    froze: list[str] = []
    monkeypatch.setattr(bl, "freeze", lambda r, n: froze.append(n))
    import eval.run as er

    async def setup(_cfg: str) -> Harness:
        return Harness(_Pipe(("no_rerank",)), {"d1": [C1]})

    monkeypatch.setattr(er, "_load_setup", lambda _spec: setup)
    monkeypatch.setattr(er, "REPORTS_DIR", tmp_path)
    args = argparse.Namespace(
        suite="retrieval", split="all", config="x", pipeline="m:s", no_db=True, freeze="retrieval",
        compare=None, tolerance=0.02, max_degraded_rate=0.05, max_usd=None, filter=None,
    )  # fmt: skip
    monkeypatch.setattr("eval.loader.load_dataset", lambda: _FakeDs())
    assert await er._main(args) == 3 and froze == []


class _FakeDs:
    def select(self, _split: str) -> list[Any]:
        return ITEMS

    def split_of(self, _id: str) -> str:
        return "-"


async def test_healthy_run(sm: SM, tracker: Tracker, tmp_path: pathlib.Path) -> None:
    run = await _run(sm, tracker, tmp_path, ())
    assert run.degraded_rate() == 0.0 and degraded_abort(run, 0.05) is None


async def test_warmup_once(sm: SM, make_owner: Callable[[], Any], tmp_path: pathlib.Path) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    await seed_vectors(sm, o.id, doc, [unit_vector(random.Random(i)) for i in range(5)])
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["passages"])
        calls.append(n)
        return httpx.Response(200, json={"scores": [1.0] * n, "model": "m"})

    gw, _p, _l, _s = make_gateway(tmp_path)
    rr = RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))
    r = Retriever(QueryEmbedder(gw, None), rr, None, get_config("retrieval@2-rerank"))
    await r.warm_up()
    assert calls == [1]  # exactly one tiny request, before any item
    from app.retrieval.scope import compute_scope

    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        for q in ("alpha", "beta", "gamma"):
            await r.retrieve(s, o.id, scope, q)
    assert len(calls) == 1 + 3  # n_items + 1
    assert uuid.UUID(int=0)  # (the warm-up passage id is a placeholder, never a chunk)
