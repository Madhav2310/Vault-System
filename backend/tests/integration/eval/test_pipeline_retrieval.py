"""S11.01: the retrieval-only pipeline makes no chat calls, reuses ingested documents, is deterministic."""

import json
import pathlib
import random
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from eval.pipeline_retrieval import ensure_documents, make_pipeline, sha256_of
from eval.schema import EvalContext, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path)
from app.retrieval.config import get_config
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from tests.support.docs import seed_document
from tests.support.llm import make_gateway
from tests.support.retrieval import seed_text_chunk
from tests.support.vectors import seed_vectors, unit_vector

SM = async_sessionmaker[AsyncSession]


def _reranker() -> RerankClient:
    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["passages"])
        return httpx.Response(200, json={"scores": [1.0 - i / 100 for i in range(n)], "model": "m"})

    return RerankClient("http://reranker.test", 800, transport=httpx.MockTransport(handler))


async def _world(sm: SM, make_owner: Callable[[], Any], tmp_path: pathlib.Path) -> tuple[Any, Any, Any]:
    o = await make_owner()
    f = tmp_path / "a.txt"
    f.write_text("contract bytes " + uuid.uuid4().hex)
    doc = await seed_document(sm, o.id, content=f.read_bytes())
    await seed_vectors(sm, o.id, doc, [unit_vector(random.Random(i)) for i in range(12)])
    await seed_text_chunk(sm, o.id, doc, "Either party may terminate on thirty days notice.", ordinal=50)
    return o, doc, f


async def _run_twice(tmp_path: pathlib.Path, sm: SM, make_owner: Callable[[], Any]) -> None:
    from app.llm.gateway import DbLedgerWriter

    o, doc, _f = await _world(sm, make_owner, tmp_path)
    gw, _p, _l, _s = make_gateway(tmp_path, ledger=DbLedgerWriter(sm))
    pipe = make_pipeline(
        sm,
        Retriever(QueryEmbedder(gw, None), _reranker(), None, get_config()),
        o.id,
        {"d": str(doc.document_id)},
    )
    q = Query("i1", "termination notice", "lookup", ("d",))
    a = await pipe(q, EvalContext())
    b = await pipe(q, EvalContext())
    return o, a, b


async def test_no_chat_calls(tmp_path: pathlib.Path, sm: SM, make_owner: Callable[[], Any]) -> None:
    o, a, _b = await _run_twice(tmp_path, sm, make_owner)
    assert a.retrieved
    async with sm() as s:
        purposes = (
            (await s.execute(text("SELECT DISTINCT purpose FROM llm_calls WHERE owner_id = :o"), {"o": o.id}))
            .scalars()
            .all()
        )
    assert purposes == ["embed_query"]


async def test_deterministic(tmp_path: pathlib.Path, sm: SM, make_owner: Callable[[], Any]) -> None:
    _o, a, b = await _run_twice(tmp_path, sm, make_owner)
    assert a.retrieved and a.retrieved == b.retrieved


async def test_reuses_ingested_docs(tmp_path: pathlib.Path, sm: SM, make_owner: Callable[[], Any]) -> None:
    o, doc, f = await _world(sm, make_owner, tmp_path)

    def boom(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request expected: the document is already ingested")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom), base_url="http://x") as http:
        ids = await ensure_documents(sm, http, o.id, {"d": f})
    assert ids == {"d": str(doc.document_id)} and sha256_of(f) == doc.sha256
    async with sm() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM document_versions WHERE owner_id = :o"), {"o": o.id})
        ).scalar_one()
    assert n == 1
