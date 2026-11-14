"""Embedding batches and brief map calls run concurrently, deterministically, within the cap."""

import asyncio
import pathlib
import time
from collections.abc import Sequence
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.settings import get_settings
from app.ingestion.embed_index import EmbedStage
from app.ingestion.errors import RetryableStageError
from app.ingestion.jobs import claim
from app.ingestion.runner import StageContext
from app.llm.providers import fake
from app.llm.types import EmbeddingResponse
from tests.integration.insights.test_brief_stage import _group_replies, _three_groups
from tests.support.brief_kit import brief_gateway, merge_reply
from tests.support.brief_seed import run_brief
from tests.support.embed import add_chunks, db_gateway
from tests.support.jobs import make_job


async def _claimed(sm, owner_id):  # type: ignore[no-untyped-def]
    ref = await make_job(sm, owner_id, stage="embed")
    async with sm() as s:
        job = await claim(s, ref.job_id, "w")
    assert job is not None
    return ref, job


def _ctx(sm, job, gw, concurrency):  # type: ignore[no-untyped-def]
    st = get_settings().model_copy(update={"embed_concurrency": concurrency})
    return StageContext(job, sm, {"gateway": gw, "settings": st})


async def test_embed_batches_concurrent(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 8 * 128)  # 8 batches
    gw, prov = db_gateway(tmp_path, sm)
    prov.latency_s = 0.5
    t = time.monotonic()
    res = await EmbedStage().run(_ctx(sm, job, gw, 4))
    elapsed = time.monotonic() - t
    assert res.detail["calls"] == 8 and len(prov.embed_calls) == 8
    assert elapsed <= 2.0, f"{elapsed:.2f} s (sequential would be >= 4 s)"


class _FailsOneBatch(fake.FakeProvider):
    """Embedding fails for the batch that contains `marker`; every other batch succeeds."""

    marker: str = ""

    async def embed(self, model: str, texts: Sequence[str], dimensions: int) -> EmbeddingResponse:
        if self.marker in texts:
            self.embed_calls.append((model, tuple(texts), dimensions))
            raise fake.overloaded()
        return await super().embed(model, texts, dimensions)


async def test_embed_failure_and_resume(sm, make_owner, tmp_path: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    from app.llm.gateway import DbLedgerWriter
    from tests.support.llm import make_gateway

    o = await make_owner()
    ref, job = await _claimed(sm, o.id)
    await add_chunks(sm, o.id, ref, 300)  # batches of 128, 128, 44
    async with sm() as s:
        texts = (
            (
                await s.execute(
                    text("SELECT embedding_text FROM chunks WHERE version_id = :v ORDER BY ordinal"),
                    {"v": ref.version_id},
                )
            )
            .scalars()
            .all()
        )
    bad = _FailsOneBatch()
    bad.marker = texts[256]  # first chunk of the third batch
    gw, _p, _l, _s = make_gateway(tmp_path, ledger=DbLedgerWriter(sm))
    gw.embeddings = bad  # type: ignore[attr-defined]
    with pytest.raises(RetryableStageError):
        await EmbedStage().run(_ctx(sm, job, gw, 3))
    async with sm() as s:
        filled = (
            await s.execute(
                text("SELECT count(embedding) FROM chunks WHERE version_id = :v"), {"v": ref.version_id}
            )
        ).scalar_one()
    assert filled in (0, 128, 256)  # whatever finished is kept; the failed batch never is
    gw2, prov2 = db_gateway(tmp_path, sm)
    res = await EmbedStage().run(_ctx(sm, job, gw2, 3))
    assert res.detail["embedded"] == 300 - filled
    assert sum(len(c[1]) for c in prov2.embed_calls) == 300 - filled  # the rerun embeds only the missing rows


class _KeyedBrief(fake.FakeProvider):
    """Replies chosen by the evidence in the request (concurrent calls arrive in any order)."""

    keyed: tuple[tuple[str, fake.FakeReply], ...] = ()
    merge: fake.FakeReply | None = None

    async def _next(self) -> fake.FakeReply:
        content = str(self.requests[-1].messages[0]["content"])
        reply = (
            next((r for kw, r in self.keyed if kw in content), None)
            if "<evidence>" in content
            else self.merge
        )
        assert reply is not None, content[:80]
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        return reply


async def test_brief_map_concurrent_deterministic(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    from tests.support.brief_seed import insight_rows

    _, seeded = await _three_groups(sm, make_owner)
    results = []
    for conc, latency in ((1, 0.0), (3, 0.5)):
        _, seeded = await _three_groups(sm, make_owner)
        gw, _p, _ = brief_gateway()
        prov = _KeyedBrief()
        prov.keyed = (
            ("Medalist", _group_replies()[0]),
            ("reports", _group_replies()[1]),
            ("Delaware", _group_replies()[2]),
        )
        prov.merge = merge_reply()
        prov.latency_s = latency
        gw.chat = prov  # type: ignore[attr-defined]
        t = time.monotonic()
        out = await run_brief(
            sm, seeded.ref.job_id, gw, enqueued, brief_max_evidence_tokens=100, brief_concurrency=conc
        )
        assert out == "finished"
        (row,) = await insight_rows(sm, seeded.ref.version_id)
        results.append(([(f["field"], f["value"]) for f in row["content"]["facts"]], time.monotonic() - t))
    assert results[0][0] == results[1][0]  # same merged output as the sequential run
    assert results[1][1] <= 1.7  # group 0, then groups 1-2 together (+ merge), not 4 x 0.5 s sequentially


async def test_brief_cost_cap_concurrent(sm, make_owner, enqueued) -> None:  # type: ignore[no-untyped-def]
    from tests.support.brief_seed import insight_rows

    _, seeded = await _three_groups(sm, make_owner)
    gw, prov, ledger = brief_gateway([*_group_replies(), merge_reply()])
    out = await run_brief(
        sm,
        seeded.ref.job_id,
        gw,
        enqueued,
        brief_max_evidence_tokens=100,
        brief_concurrency=3,
        brief_max_usd=1e-12,
    )
    assert out == "finished"
    assert len(prov.requests) == 1, "the cap stopped every call after the first, even with concurrency 3"
    (row,) = await insight_rows(sm, seeded.ref.version_id)
    assert row["content"]["partial"] is True
    assert Decimal(0) <= sum((r.cost_usd for r in ledger.records), Decimal(0))
