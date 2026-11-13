"""S17.04: a section reference in the query finds its chunk through the real `tsv_ids` population."""

from typing import Any

from app.ingestion.identifiers import fill_tsv_ids
from app.retrieval.lexical import lexical_search
from app.retrieval.scope import compute_scope
from tests.support.docs import seed_document
from tests.support.retrieval import bulk_filler_chunks, seed_text_chunk


async def test_section_ref_first(sm: Any, make_owner: Any) -> None:
    o = await make_owner()
    doc = await seed_document(sm, o.id)
    await bulk_filler_chunks(sm, o.id, doc, 50, tag="x")
    near = await seed_text_chunk(sm, o.id, doc, "Sections 7 and 3 are summarised in the annex.", ordinal=0)
    target = await seed_text_chunk(
        sm, o.id, doc, "Section 7.3 limits the liability of the Supplier.", ordinal=1
    )
    async with sm() as s:
        await fill_tsv_ids(s, o.id, doc.version_id)
        await s.commit()
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        hits = await lexical_search(s, o.id, scope, "Section 7.3")
    assert hits[0].chunk_id == target and hits[0].id_match
    assert near not in [h.chunk_id for h in hits if h.id_match]
