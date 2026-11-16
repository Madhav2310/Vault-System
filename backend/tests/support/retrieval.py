"""Seed chunks with chosen text for retrieval tests (the database generates tsv_en from embedding_text)."""

import uuid
from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.retrieval.config import RetrievalConfig, get_config
from app.retrieval.query_embed import QueryEmbedder
from app.retrieval.rerank import DEGRADED_NO_RERANK, FAIL_HTTP, RerankedItem, RerankPassage, RerankResult
from app.retrieval.retrieve import Retriever
from tests.support.docs import Seeded

_INSERT = (
    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
    "embedding_text, page_start, page_end, sentences, spans, token_count, content_hash{extra_cols}) "
    "VALUES (:id, :o, :d, :v, :ord, 'text', :t, :t, 1, 1, CAST('[]' AS jsonb), CAST('[]' AS jsonb), 5, "
    "'h'{extra_vals})"
)


async def seed_text_chunk(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    seeded: Seeded,
    body: str,
    *,
    ordinal: int,
    simple_ids_from_text: bool = False,
) -> uuid.UUID:
    """Insert one chunk whose searchable text is `body`.

    `simple_ids_from_text` fills `tsv_ids` with `to_tsvector('simple', body)` as a test stand-in that
    treats every word as an identifier token.
    """
    cid = uuid7()
    sql = _INSERT.format(
        extra_cols=", tsv_ids" if simple_ids_from_text else "",
        extra_vals=", to_tsvector('simple', :t)" if simple_ids_from_text else "",
    )
    async with sm() as s:
        await s.execute(
            text(sql),
            {
                "id": cid,
                "o": owner_id,
                "d": seeded.document_id,
                "v": seeded.version_id,
                "ord": ordinal,
                "t": body,
            },
        )
        await s.commit()
    return cid


async def bulk_filler_chunks(
    sm: async_sessionmaker[AsyncSession], owner_id: uuid.UUID, seeded: Seeded, n: int, *, tag: str
) -> None:
    """`n` synthetic chunks for one version, in a single statement (ordinals 1000..)."""
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, original_text, "
                "embedding_text, page_start, page_end, sentences, spans, token_count, content_hash) "
                "SELECT gen_random_uuid(), :o, :d, :v, 1000 + g, 'text', "
                "'filler ' || :tag || ' clause ' || g || ' obligations of the parties ' || (g % 97), "
                "'filler ' || :tag || ' clause ' || g || ' obligations of the parties ' || (g % 97), "
                "1, 1, CAST('[]' AS jsonb), CAST('[]' AS jsonb), 12, 'h' FROM generate_series(1, :n) g"
            ),
            {"o": owner_id, "d": seeded.document_id, "v": seeded.version_id, "tag": tag, "n": n},
        )
        await s.commit()


class StubReranker:
    """The LLM reranker's contract (`LlmReranker.rerank`) without a model: it keeps the fused order and ranks
    only (no scores), or fails like an unavailable model (`no_rerank`). It records the passages sent."""

    def __init__(self, *, fail: bool = False, on_call: Callable[[], None] | None = None) -> None:
        self.fail, self.on_call = fail, on_call
        self.sent: list[str] = []

    async def rerank(
        self, query: str, passages: Sequence[RerankPassage], top_n: int, **_: object
    ) -> RerankResult:
        self.sent.extend(p.text for p in passages)
        if self.on_call is not None:
            self.on_call()
        kept = [RerankedItem(p.chunk_id, i, None) for i, p in enumerate(passages[:top_n])]
        if self.fail:
            return RerankResult(kept, [None] * len(passages), True, DEGRADED_NO_RERANK, FAIL_HTTP, None)
        return RerankResult(kept, [None] * len(passages), False, None, None, "stub")


def stub_retriever(
    embedder: QueryEmbedder, redis: Any = None, cfg: RetrievalConfig | None = None, **kw: Any
) -> Retriever:
    """A `Retriever` on `cfg` (default `retrieval@5`, the LLM reranker) with a `StubReranker`."""
    return Retriever(embedder, redis, cfg or get_config("retrieval@5"), llm_reranker=StubReranker(**kw))  # type: ignore[arg-type]
