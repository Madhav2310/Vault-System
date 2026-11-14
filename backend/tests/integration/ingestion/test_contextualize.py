"""Contextual chunk headers: index side only (original text untouched), re-embed changed chunks only,
idempotent, failure isolated. Offline: FakeProvider, real database."""

import json
import pathlib
from typing import Any

from sqlalchemy import text

from app.core.ids import uuid7
from app.core.settings import get_settings
from app.ingestion.contextualize import ContextualizeStage, build_embedding_text
from app.ingestion.runner import run_job
from app.insights.brief import EMPTY_PARAMS_HASH
from app.llm.gateway import DbLedgerWriter
from app.llm.providers import fake
from tests.support.chat_gen import fake_gateway
from tests.support.embed import add_chunks, db_gateway
from tests.support.jobs import make_job

SUMMARY = "A services agreement between Acme and Globex. It runs for twelve months."
STRUCT = "Document: T | Section: -"


def _services(gw: Any, store: Any, variant: str, **kw: Any) -> dict[str, Any]:
    settings = get_settings().model_copy(update={"contextualize_variant": variant, **kw})
    return {"gateway": gw, "settings": settings, "store": store}


async def _world(sm: Any, make_owner: Any, n: int = 3, brief: bool = True) -> tuple[Any, Any, list[Any]]:
    o = await make_owner()
    ref = await make_job(sm, o.id, stage="contextualize")
    ids = await add_chunks(sm, o.id, ref, n, embedded=True)
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = 'enriched' WHERE id = :v"), {"v": ref.version_id}
        )
        await s.execute(
            text(
                "UPDATE chunks SET structural_prefix = :sp, section_path = '{Term,Fees}', "
                "sentences = CAST(:sent AS jsonb) WHERE version_id = :v"
            ),
            {"sp": STRUCT, "sent": json.dumps([[0, 5], [6, 9]]), "v": ref.version_id},
        )
        if brief:
            content = {"document_type": "Services Agreement", "summary": SUMMARY}
            await s.execute(
                text(
                    "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, content, "
                    "status, prompt_version) VALUES (:i, :o, :v, 'brief', :h, CAST(:c AS jsonb), 'done', 1)"
                ),
                {
                    "i": uuid7(),
                    "o": o.id,
                    "v": ref.version_id,
                    "h": EMPTY_PARAMS_HASH,
                    "c": json.dumps(content),
                },
            )
        await s.commit()
    return o, ref, ids


async def _rows(sm: Any, version_id: Any) -> list[Any]:
    async with sm() as s:
        return list(
            await s.execute(
                text(
                    "SELECT id, original_text, sentences, spans, content_hash, contextual_prefix, "
                    "embedding_text, embedding::text AS emb, tsv_en::text AS tsv FROM chunks "
                    "WHERE version_id = :v ORDER BY ordinal"
                ),
                {"v": version_id},
            )
        )


async def _run(sm: Any, ref: Any, services: dict[str, Any], enqueued: Any) -> str:
    return await run_job(
        sm, ref.job_id, "w", {"contextualize": ContextualizeStage()}, enqueued,
        order=("contextualize",), services=services,
    )  # fmt: skip


def _embedded_texts(prov: Any) -> int:
    return sum(len(c[1]) for c in prov.embed_calls)


async def test_original_text_unchanged(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    _o, ref, _ = await _world(sm, make_owner)
    before = await _rows(sm, ref.version_id)
    gw, prov = db_gateway(tmp_path, sm)
    assert await _run(sm, ref, _services(gw, store, "deterministic"), enqueued) == "finished"
    after = await _rows(sm, ref.version_id)
    prefix = "Services Agreement: A services agreement between Acme and Globex. | Section: Term > Fees"
    for b, a in zip(before, after, strict=True):
        assert (a.original_text, a.sentences, a.spans, a.content_hash) == (
            b.original_text, b.sentences, b.spans, b.content_hash,
        )  # fmt: skip
        assert a.contextual_prefix == prefix
        assert a.embedding_text == f"{STRUCT}\n{prefix}\n{a.original_text}"
        assert prefix not in a.original_text
        assert "globex" in a.tsv and "globex" not in b.tsv  # the lexical index follows embedding_text
        assert a.emb != b.emb  # re-embedded
    assert _embedded_texts(prov) == 3


async def test_reembed_changed_only(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    _o, ref, ids = await _world(sm, make_owner)
    prefix = "Services Agreement: A services agreement between Acme and Globex. | Section: Term > Fees"
    first = (await _rows(sm, ref.version_id))[0]
    async with sm() as s:  # chunk 0 is already contextualized with the same header
        await s.execute(
            text("UPDATE chunks SET contextual_prefix = :p, embedding_text = :t WHERE id = :i"),
            {"p": prefix, "t": build_embedding_text(STRUCT, prefix, first.original_text), "i": ids[0]},
        )
        await s.commit()
    gw, prov = db_gateway(tmp_path, sm)
    assert await _run(sm, ref, _services(gw, store, "deterministic"), enqueued) == "finished"
    assert _embedded_texts(prov) == 2  # the changed chunks only
    rows = await _rows(sm, ref.version_id)
    assert rows[0].emb == first.emb and rows[1].emb != first.emb


async def test_idempotent(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    _o, ref, _ = await _world(sm, make_owner)
    gw, prov = db_gateway(tmp_path, sm)
    services = _services(gw, store, "deterministic")
    assert await _run(sm, ref, services, enqueued) == "finished"
    once = await _rows(sm, ref.version_id)
    assert _embedded_texts(prov) == 3
    async with sm() as s:  # the job is finished: re-deliver it
        await s.execute(
            text(
                "UPDATE processing_jobs SET status = 'pending', current_stage = 'contextualize' WHERE id = :j"
            ),
            {"j": ref.job_id},
        )
        await s.commit()
    assert await _run(sm, ref, services, enqueued) == "finished"
    assert _embedded_texts(prov) == 3  # second run: zero embed calls
    assert [r.emb for r in await _rows(sm, ref.version_id)] == [r.emb for r in once]


async def test_failure_isolated(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    _o, ref, _ = await _world(sm, make_owner)
    before = await _rows(sm, ref.version_id)
    gw, _ = db_gateway(tmp_path, sm, [fake.overloaded()] * 4)  # every embedding attempt fails
    assert await _run(sm, ref, _services(gw, store, "deterministic"), enqueued) == "finished"
    after = await _rows(sm, ref.version_id)
    assert [(a.embedding_text, a.emb, a.contextual_prefix) for a in after] == [
        (b.embedding_text, b.emb, b.contextual_prefix) for b in before
    ]
    async with sm() as s:
        status = (
            await s.execute(text("SELECT status FROM document_versions WHERE id = :v"), {"v": ref.version_id})
        ).scalar_one()
        events = (
            (await s.execute(text("SELECT detail FROM job_events WHERE job_id = :j"), {"j": ref.job_id}))
            .scalars()
            .all()
        )
    assert status == "enriched" and any("failed" in (e or {}) for e in events)


async def test_off_and_no_brief_do_nothing(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    _o, ref, _ = await _world(sm, make_owner, brief=False)
    gw, prov = db_gateway(tmp_path, sm)
    assert await _run(sm, ref, _services(gw, store, "off"), enqueued) == "finished"
    assert not prov.embed_calls and not prov.requests
    _o2, ref2, _ = await _world(sm, make_owner, brief=False)
    assert await _run(sm, ref2, _services(gw, store, "deterministic"), enqueued) == "finished"
    assert not prov.embed_calls and all(r.contextual_prefix is None for r in await _rows(sm, ref2.version_id))


async def test_llm_variant_generates_once(
    sm: Any, make_owner: Any, store: Any, enqueued: Any, tmp_path: pathlib.Path
) -> None:
    o, ref, _ = await _world(sm, make_owner, n=2)
    long = " ".join(f"w{i}" for i in range(80))
    gw, prov = fake_gateway(  # the real prompt registry: chunk_context@1 must resolve
        [
            fake.structured({"context": "Fee clause of the Acme agreement."}),
            fake.structured({"context": long}),
        ],
        ledger=DbLedgerWriter(sm),
    )
    services = _services(gw, store, "llm")
    assert await _run(sm, ref, services, enqueued) == "finished"
    rows = await _rows(sm, ref.version_id)
    assert rows[0].contextual_prefix == "Fee clause of the Acme agreement."
    assert len(rows[1].contextual_prefix.split()) == 50  # clipped by code
    assert len(prov.requests) == 2 and _embedded_texts(prov) == 2
    async with sm() as s:
        calls = (
            await s.execute(
                text(
                    "SELECT count(*), bool_and(job_id = :j), bool_and(role = 'fast') FROM llm_calls "
                    "WHERE owner_id = :o AND purpose = 'contextualize'"
                ),
                {"j": ref.job_id, "o": o.id},
            )
        ).one()
        await s.execute(
            text(
                "UPDATE processing_jobs SET status = 'pending', current_stage = 'contextualize' WHERE id = :j"
            ),
            {"j": ref.job_id},
        )
        await s.commit()
    assert tuple(calls) == (2, True, True)
    assert await _run(sm, ref, services, enqueued) == "finished"
    assert len(prov.requests) == 2 and _embedded_texts(prov) == 2  # headers exist: nothing regenerated


async def test_contextualized_chunks_are_not_a_reuse_source(
    sm: Any, make_owner: Any, tmp_path: pathlib.Path
) -> None:
    """A vector computed from a header-enriched text is never copied to a version without a header."""
    from app.ingestion.embed_index import EmbedStage
    from app.ingestion.jobs import claim
    from tests.support.embed import stage_ctx

    o = await make_owner()
    ref1 = await make_job(sm, o.id, stage="embed")
    await add_chunks(sm, o.id, ref1, 2, embedded=True)
    async with sm() as s:
        await s.execute(
            text("UPDATE chunks SET contextual_prefix = 'ctx' WHERE version_id = :v"), {"v": ref1.version_id}
        )
        await s.commit()
    ref2 = await make_job(sm, o.id, stage="embed")
    await add_chunks(sm, o.id, ref2, 2)  # same texts and hashes, no embeddings
    async with sm() as s:
        job = await claim(s, ref2.job_id, "w")
    assert job is not None
    gw, prov = db_gateway(tmp_path, sm)
    res = await EmbedStage().run(stage_ctx(sm, job, gw))
    assert res.detail and res.detail["reused"] == 0 and _embedded_texts(prov) == 2


async def _second_doc(sm: Any, owner_id: Any) -> Any:
    ref = await make_job(sm, owner_id, stage="contextualize")
    await add_chunks(sm, owner_id, ref, 2, tag="b", embedded=True)
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = 'ready' WHERE id = :v"), {"v": ref.version_id}
        )
        await s.execute(
            text(
                "INSERT INTO document_insights (id, owner_id, version_id, kind, params_hash, content, "
                "status, prompt_version) VALUES (:i, :o, :v, 'brief', :h, CAST(:c AS jsonb), 'done', 1)"
            ),
            {
                "i": uuid7(),
                "o": owner_id,
                "v": ref.version_id,
                "h": EMPTY_PARAMS_HASH,
                "c": json.dumps({"document_type": "Lease", "summary": "A lease. Two years."}),
            },
        )
        await s.commit()
    await _current(sm, ref.version_id)
    return ref


async def _current(sm: Any, version_id: Any) -> None:
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE documents SET current_version_id = :v WHERE id = "
                "(SELECT document_id FROM document_versions WHERE id = :v)"
            ),
            {"v": version_id},
        )
        await s.commit()


async def test_backfill_idempotent_and_original_untouched(
    sm: Any, make_owner: Any, tmp_path: pathlib.Path
) -> None:
    from app.ingestion.contextualize import backfill_owner

    o, ref, _ = await _world(sm, make_owner)
    await _current(sm, ref.version_id)
    ref2 = await _second_doc(sm, o.id)
    before = await _rows(sm, ref.version_id) + await _rows(sm, ref2.version_id)
    gw, prov = db_gateway(tmp_path, sm)
    settings = get_settings()
    first = await backfill_owner(sm, gw, settings, o.id, "deterministic")
    assert (first.documents, first.chunks_changed, first.failed) == (2, 5, 0)
    assert _embedded_texts(prov) == 5
    after = await _rows(sm, ref.version_id) + await _rows(sm, ref2.version_id)
    for b, a in zip(before, after, strict=True):
        assert (a.original_text, a.sentences, a.spans, a.content_hash) == (
            b.original_text, b.sentences, b.spans, b.content_hash,
        )  # fmt: skip
        assert a.contextual_prefix and a.embedding_text.count(a.original_text) == 1
    second = await backfill_owner(sm, gw, settings, o.id, "deterministic")
    assert (second.documents, second.chunks_changed) == (2, 0)
    assert _embedded_texts(prov) == 5  # zero embed calls on the second run
    again = await _rows(sm, ref.version_id) + await _rows(sm, ref2.version_id)
    assert [r.emb for r in again] == [r.emb for r in after]


async def test_backfill_owner_scoped(sm: Any, make_owner: Any, tmp_path: pathlib.Path) -> None:
    from app.ingestion.contextualize import backfill_owner

    o, ref, _ = await _world(sm, make_owner)
    await _current(sm, ref.version_id)
    other, oref, _ = await _world(sm, make_owner)
    await _current(sm, oref.version_id)
    before = await _rows(sm, oref.version_id)
    gw, _ = db_gateway(tmp_path, sm)
    res = await backfill_owner(sm, gw, get_settings(), o.id, "deterministic")
    assert (res.documents, res.chunks_changed) == (1, 3)
    after = await _rows(sm, oref.version_id)
    assert [(a.embedding_text, a.emb, a.contextual_prefix) for a in after] == [
        (b.embedding_text, b.emb, b.contextual_prefix) for b in before
    ]  # the other owner's chunks were never read or written
    assert other.id != o.id


async def test_backfill_cost_cap_stops(sm: Any, make_owner: Any, tmp_path: pathlib.Path) -> None:
    from decimal import Decimal

    from app.ingestion.contextualize import backfill_owner

    o, ref, _ = await _world(sm, make_owner, n=2)
    await _current(sm, ref.version_id)
    ref2 = await _second_doc(sm, o.id)
    gw, prov = fake_gateway(
        [fake.structured({"context": f"Header {i}."}) for i in range(8)], ledger=DbLedgerWriter(sm)
    )
    res = await backfill_owner(sm, gw, get_settings(), o.id, "llm", Decimal("0.000000001"))
    assert res.stopped == "budget" and res.documents == 1  # the second document never started
    assert len(prov.requests) == 1  # the first wave is one call; the cap then blocks the rest
    assert res.spend_usd > 0
    assert all(r.contextual_prefix is None for r in await _rows(sm, ref2.version_id))
