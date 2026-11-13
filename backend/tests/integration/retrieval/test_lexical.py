"""S2.05 lexical search: ranking, identifier match, safety, scope isolation, and the physical plan."""

import json
import random
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.lexical import lexical_search, lexical_statement
from app.retrieval.scope import Scope, compute_scope
from tests.support.docs import seed_document
from tests.support.retrieval import bulk_filler_chunks, seed_text_chunk

SM = async_sessionmaker[AsyncSession]


async def test_phrase_first(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    scattered = await seed_text_chunk(
        sm, o.id, doc, "Payment is due. Net terms apply; thirty days is long.", ordinal=0
    )
    phrase = await seed_text_chunk(
        sm, o.id, doc, "Invoices are payable net thirty days from receipt.", ordinal=1
    )
    other = await seed_text_chunk(
        sm, o.id, doc, "Days of thirty shall pass; the net result is nil.", ordinal=2
    )
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        quoted = await lexical_search(s, o.id, scope, '"net thirty days"')
        plain = await lexical_search(s, o.id, scope, "net thirty days")
    assert [h.chunk_id for h in quoted] == [phrase]  # a quoted phrase matches only the adjacent words
    assert plain[0].chunk_id == phrase
    assert {h.chunk_id for h in plain} == {phrase, scattered, other}
    assert all(h.version_id == doc.version_id and h.score > 0 and not h.id_match for h in plain)
    assert [h.score for h in plain] == sorted((h.score for h in plain), reverse=True)


async def test_identifier_match(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    target = await seed_text_chunk(
        sm,
        o.id,
        doc,
        "Termination is governed by 4.2(b) of this agreement.",
        ordinal=0,
        simple_ids_from_text=True,
    )
    await seed_text_chunk(
        sm, o.id, doc, "Section headings and general provisions.", ordinal=1, simple_ids_from_text=True
    )
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        hits = await lexical_search(s, o.id, scope, "section 4.2(b)")
        no_ids = await lexical_search(s, o.id, scope, "general provisions")
    assert hits[0].chunk_id == target and hits[0].id_match is True
    assert all(not h.id_match for h in no_ids)
    # a chunk matched only through its identifier token (english part of the query absent from the text)
    async with sm() as s:
        only_id = await lexical_search(s, o.id, scope, "unrelatedword 4.2(b)")
    assert [h.chunk_id for h in only_id] == [target] and only_id[0].id_match is True


async def test_stopwords_only(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    await seed_text_chunk(sm, o.id, doc, "The of and to in a.", ordinal=0)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        assert await lexical_search(s, o.id, scope, "the of and") == []
        assert await lexical_search(s, o.id, scope, "   ") == []
        assert await lexical_search(s, o.id, scope, "") == []


async def test_malformed_query_safe(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    cid = await seed_text_chunk(sm, o.id, doc, "Quote the termination clause carefully (see 4.2).", ordinal=0)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        qs = [
            '"unbalanced quote',
            'termination "clause',
            "((",
            "a & | ! b",
            "-",
            '""',
            "x\x00y",
            "'; DROP TABLE chunks;--",
        ]
        for q in qs:
            await lexical_search(s, o.id, scope, q)  # must not raise
        assert [h.chunk_id for h in await lexical_search(s, o.id, scope, '"termination clause')] == [cid]
        # the session is still usable afterwards
        assert (await s.execute(text("SELECT 1"))).scalar_one() == 1


async def test_never_outside_scope(sm: SM, make_owner: Callable[[], Any]) -> None:
    """Randomised property: for any subset scope, hits are within it; other owners and states never leak."""
    a, b = await make_owner(), await make_owner()
    docs = [await seed_document(sm, a.id) for _ in range(4)]
    chunk_version: dict[Any, Any] = {}
    for i, d in enumerate(docs):
        for j in range(3):
            cid = await seed_text_chunk(sm, a.id, d, f"shared needle text {i} {j}", ordinal=j)
            chunk_version[cid] = d.version_id
    theirs = await seed_document(sm, b.id)
    foreign = await seed_text_chunk(sm, b.id, theirs, "shared needle text foreign", ordinal=0)
    unready = await seed_document(sm, a.id, status="processing")
    await seed_text_chunk(sm, a.id, unready, "shared needle text processing", ordinal=0)
    gone = await seed_document(sm, a.id)
    await seed_text_chunk(sm, a.id, gone, "shared needle text deleted", ordinal=0)
    async with sm() as s:
        await s.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :d"), {"d": gone.document_id}
        )
        await s.commit()
    rng = random.Random(205)
    async with sm() as s:
        full = await compute_scope(s, a.id, None)
        assert set(full.version_ids) == {d.version_id for d in docs}
        for _ in range(25):
            picks = rng.sample(docs, rng.randint(0, len(docs)))
            sub = Scope(entries=tuple(e for e in full.entries if e[1] in {p.version_id for p in picks}))
            hits = await lexical_search(s, a.id, sub, "shared needle", k=100)
            allowed = set(sub.version_ids)
            assert {h.version_id for h in hits} <= allowed
            assert {h.chunk_id for h in hits} == {c for c, v in chunk_version.items() if v in allowed}
        # even a scope that wrongly names another owner's version cannot return their chunks (owner predicate)
        forged = Scope(entries=(*full.entries, (theirs.document_id, theirs.version_id)))
        leaked = {h.chunk_id for h in await lexical_search(s, a.id, forged, "shared needle", k=100)}
        assert foreign not in leaked
        assert foreign not in {
            h.chunk_id for h in await lexical_search(s, a.id, Scope.empty, "shared needle")
        }
        # the foreign owner sees only their own chunk
        bscope = await compute_scope(s, b.id, None)
        assert [h.chunk_id for h in await lexical_search(s, b.id, bscope, "shared needle")] == [foreign]


async def test_stable_tiebreak_and_k(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    ids = [await seed_text_chunk(sm, o.id, doc, "identical wording here", ordinal=i) for i in range(5)]
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        top = await lexical_search(s, o.id, scope, "identical wording", k=3)
        again = await lexical_search(s, o.id, scope, "identical wording", k=3)
    assert [h.chunk_id for h in top] == sorted(ids)[:3] == [h.chunk_id for h in again]
    async with sm() as s:
        assert await lexical_search(s, o.id, scope, "identical", k=0) == []


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


async def _explain(s: AsyncSession, sql: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = (await s.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params)).scalar_one()
    plan: dict[str, Any] = (raw if isinstance(raw, list) else json.loads(raw))[0]["Plan"]
    return plan


def _summary(plan: dict[str, Any]) -> tuple[set[str], list[str], str]:
    nodes = _nodes(plan)
    scanned = {n["Relation Name"] for n in nodes if n.get("Relation Name", "").startswith("chunks_p")}
    idx = [n["Node Type"] + ":" + n["Index Name"] for n in nodes if "Index Name" in n]
    text_ = f"partitions={sorted(scanned)} nodes={[n['Node Type'] for n in nodes]} indexes={idx}"
    return scanned, idx, text_ + f" cost={plan['Total Cost']} rows_out={plan['Actual Rows']}"


async def test_explain_pruning_and_gin(sm: SM, make_owner: Callable[[], Any], capsys: Any) -> None:
    """Owner partition pruning is asserted. GIN use is asserted only once the index's pending list is flushed
    (what autovacuum does): freshly bulk-inserted rows sit in the GIN pending list, which inflates the
    planner's cost for the index so it prefers the owner btree. The plan is never forced."""
    o, other = await make_owner(), await make_owner()
    doc, odoc = await seed_document(sm, o.id), await seed_document(sm, other.id)
    await bulk_filler_chunks(sm, o.id, doc, 3000, tag="mine")
    await bulk_filler_chunks(sm, other.id, odoc, 3000, tag="theirs")
    needle = await seed_text_chunk(sm, o.id, doc, "The zyzzyva indemnity survives termination.", ordinal=0)
    async with sm() as s:
        await s.execute(text("ANALYZE chunks"))
        await s.commit()  # ANALYZE statistics are transactional
        scope = await compute_scope(s, o.id, None)
        assert [h.chunk_id for h in await lexical_search(s, o.id, scope, "zyzzyva indemnity")] == [needle]
        assert len(await lexical_search(s, o.id, scope, "obligations parties", k=40)) == 40
        sql, params = lexical_statement(o.id, scope.version_ids, "zyzzyva indemnity", 40)
        before = await _explain(s, sql, params)
        scanned, _, before_txt = _summary(before)
        assert len(scanned) == 1, before_txt  # only the owner's hash partition is touched
        (part,) = scanned
        await s.execute(text(f"SELECT gin_clean_pending_list('{part}_owner_id_tsv_en_idx')"))
        after = await _explain(s, sql, params)
        scanned2, idx2, after_txt = _summary(after)
    with capsys.disabled():
        print(f"\nEXPLAIN before pending-list flush: {before_txt}\nEXPLAIN after flush: {after_txt}")
    assert scanned2 == scanned
    assert any(i.endswith("_owner_id_tsv_en_idx") for i in idx2), after_txt
