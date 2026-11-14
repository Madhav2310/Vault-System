"""S22.03: lexical leg v2 (number/unit variants in the query, length-normalised ts_rank_cd)."""

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.config import get_config
from app.retrieval.lexical import lexical_search
from app.retrieval.scope import compute_scope
from tests.support.docs import seed_document
from tests.support.retrieval import seed_text_chunk

SM = async_sessionmaker[AsyncSession]


async def _search(sm: SM, owner_id: Any, query: str, variant: str, k: int = 10) -> list[Any]:
    async with sm() as s:
        scope = await compute_scope(s, owner_id, None)
        return await lexical_search(s, owner_id, scope, query, k=k, variant=variant)


async def test_number_variants(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    # the distractor is seeded first, so it wins every score tie on the chunk id
    distractor = await seed_text_chunk(
        sm, o.id, d, "Net revenue grew steadily across all regions this year.", ordinal=0
    )
    table = await seed_text_chunk(
        sm,
        o.id,
        d,
        "Consolidated results (in millions) | Net revenue | 1,200 | Operating income | 310",
        ordinal=1,
    )
    q = "What was net revenue of $1.2 billion?"
    v1 = [h.chunk_id for h in await _search(sm, o.id, q, "v1")]
    v2 = [h.chunk_id for h in await _search(sm, o.id, q, "v2")]
    assert v1[0] == distractor  # v1 cannot see that "1,200" is "$1.2 billion"
    assert v2[0] == table and distractor in v2


async def test_number_matches_without_other_words(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    other = await seed_text_chunk(sm, o.id, d, "Headcount was 4,100 at year end.", ordinal=0)
    billion = await seed_text_chunk(sm, o.id, d, "Backlog stood at $1.2 billion at quarter end.", ordinal=1)
    bare = await seed_text_chunk(sm, o.id, d, "Backlog | 1200", ordinal=2)
    ids = [h.chunk_id for h in await _search(sm, o.id, "backlog of 1,200 million", "v2")]
    assert billion in ids and bare in ids and other not in ids


async def test_fiscal_year_forms(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    fy = await seed_text_chunk(
        sm, o.id, d, "Segment margin improved in FY22 versus the prior period.", ordinal=0
    )
    await seed_text_chunk(sm, o.id, d, "Unrelated text about leases.", ordinal=1)
    ids = [h.chunk_id for h in await _search(sm, o.id, "fiscal year 2022 segment margin", "v2")]
    assert ids and ids[0] == fy


async def test_length_normalisation(sm: SM, make_owner: Callable[[], Any]) -> None:
    """One hit in a short and a long chunk: v1 ties (chunk id order), v2 ranks the short one first."""
    o = await make_owner()
    d = await seed_document(sm, o.id)
    filler = (
        "The company operates several segments and reports results regularly under accounting policies. " * 20
    )
    long_ = await seed_text_chunk(sm, o.id, d, f"Termination fee applies. {filler}", ordinal=0)
    short = await seed_text_chunk(sm, o.id, d, "Termination fee applies.", ordinal=1)
    v1 = await _search(sm, o.id, "termination fee", "v1")
    v2 = await _search(sm, o.id, "termination fee", "v2")
    assert v1[0].chunk_id == long_ and v1[0].score == v1[1].score  # no length effect, id tie-break
    assert [h.chunk_id for h in v2][:2] == [short, long_] and v2[0].score > v2[1].score


async def test_v2_keeps_or_semantics_and_safety(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    c = await seed_text_chunk(sm, o.id, d, "Indemnification and liability caps apply.", ordinal=0)
    for q in ["indemnification & | ! ( ) ' $1.2 billion liability", "'; DROP TABLE chunks; -- $5-10 million"]:
        await _search(sm, o.id, q, "v2")  # must not raise
    assert [h.chunk_id for h in await _search(sm, o.id, "liability", "v2")] == [c]
    assert await _search(sm, o.id, "of the and", "v2") == []
    quoted = await _search(sm, o.id, '"liability caps"', "v2")  # websearch path takes the rank flag too
    assert [h.chunk_id for h in quoted] == [c]


def test_config_version_selects_v2() -> None:
    assert get_config("retrieval@2").lexical == "v1"
    assert get_config("retrieval@2-lexical2").lexical == "v2"
