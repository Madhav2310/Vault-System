"""Lexical search with OR semantics over the query's English lexemes."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.retrieval.lexical import lexical_search, lexical_statement
from app.retrieval.scope import compute_scope
from tests.support.docs import seed_document
from tests.support.retrieval import seed_text_chunk

SM = async_sessionmaker[AsyncSession]


async def _search(sm: SM, owner_id: Any, query: str, k: int = 10) -> list[Any]:
    async with sm() as s:
        return await lexical_search(s, owner_id, await compute_scope(s, owner_id, None), query, k=k)


async def test_natural_question_matches(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    gov = await seed_text_chunk(
        sm, o.id, d, "This Agreement shall be governed by the laws of Delaware.", ordinal=0
    )
    await seed_text_chunk(sm, o.id, d, "The fee is payable monthly in arrears.", ordinal=1)
    hits = await _search(sm, o.id, "What is the governing law of this agreement?")
    assert (
        hits and hits[0].chunk_id == gov
    )  # AND semantics matched nothing ("law" + "what" + ... in one chunk)


async def test_stopwords_only_empty(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    await seed_text_chunk(sm, o.id, d, "the of and to in it is", ordinal=0)
    assert await _search(sm, o.id, "what is the of and to") == []


async def test_min_match(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    two = await seed_text_chunk(sm, o.id, d, "Termination requires thirty days written notice.", ordinal=0)
    await seed_text_chunk(sm, o.id, d, "Payment of invoices happens monthly.", ordinal=1)
    one = await seed_text_chunk(sm, o.id, d, "Notice of the meeting is posted.", ordinal=2)
    ids = {
        h.chunk_id for h in await _search(sm, o.id, "termination notice thirty days required")
    }  # 5 lexemes
    assert two in ids and one not in ids  # one lexeme ("notice") is not enough for a 4+ lexeme query
    short = {h.chunk_id for h in await _search(sm, o.id, "notice")}
    assert one in short  # a short query needs only one


async def test_min_match_counts_url_and_email_lexemes(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    both = await seed_text_chunk(sm, o.id, d, "Docs at https://example.com/a/b, ask ops@acme.io.", ordinal=0)
    one = await seed_text_chunk(sm, o.id, d, "Ask ops@acme.io only.", ordinal=1)
    ids = {h.chunk_id for h in await _search(sm, o.id, "https://example.com/a/b, ops@acme.io zebra quagga")}
    assert both in ids and one not in ids  # url and email lexemes count like words toward the minimum of 2


async def test_more_terms_rank_higher(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    few = await seed_text_chunk(sm, o.id, d, "Either party may terminate the agreement quickly.", ordinal=0)
    many = await seed_text_chunk(sm, o.id, d, "Either party may terminate on thirty days notice.", ordinal=1)
    hits = await _search(sm, o.id, "terminate notice thirty")
    order = [h.chunk_id for h in hits]
    assert order.index(many) < order.index(few)


async def test_operator_chars_safe(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    c = await seed_text_chunk(sm, o.id, d, "Indemnification and liability caps apply.", ordinal=0)
    for q in ["indemnification & | ! : * ( ) ' liability", "'; DROP TABLE chunks; --", "a:* & ((", "<-> !!"]:
        await _search(sm, o.id, q)  # never raises
    assert [h.chunk_id for h in await _search(sm, o.id, "indemnification & | ! (liability)")] == [c]


async def test_owner_isolation(sm: SM, make_owner: Callable[[], Any]) -> None:
    a, b = await make_owner(), await make_owner()
    da, db = await seed_document(sm, a.id), await seed_document(sm, b.id)
    await seed_text_chunk(sm, a.id, da, "Secret arbitration clause here.", ordinal=0)
    mine = await seed_text_chunk(sm, b.id, db, "Arbitration clause of the other owner.", ordinal=0)
    assert [h.chunk_id for h in await _search(sm, b.id, "arbitration clause")] == [mine]


async def test_explain_ok(sm: SM, make_owner: Callable[[], Any]) -> None:
    o = await make_owner()
    d = await seed_document(sm, o.id)
    await seed_text_chunk(sm, o.id, d, "Governing law Delaware.", ordinal=0)
    async with sm() as s:
        scope = await compute_scope(s, o.id, None)
        sql, params = lexical_statement(o.id, scope.version_ids, "governing law of this agreement", 5)
        await s.execute(text("EXPLAIN " + sql), params)
