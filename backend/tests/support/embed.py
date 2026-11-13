"""Helpers for embed/index stage tests: chunk rows on an existing version and a gateway over FakeProvider that
writes real `llm_calls` rows."""

import hashlib
import pathlib
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.core.settings import get_settings
from app.ingestion.runner import StageContext
from app.llm.gateway import DbLedgerWriter, LlmGateway
from app.llm.providers import fake
from tests.support.jobs import JobRef
from tests.support.llm import make_gateway


async def add_chunks(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    ref: JobRef,
    n: int,
    *,
    tag: str = "a",
    embedded: bool = False,
) -> list[uuid.UUID]:
    ids = []
    async with sm() as s:
        for i in range(n):
            cid, body = uuid7(), f"Chunk {tag} {i}: the supplier shall deliver goods."
            emb = f"Document: T | Section: -\n{body}"
            await s.execute(
                text(
                    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                    "original_text, embedding_text, page_start, page_end, sentences, spans, token_count, "
                    "content_hash"
                    + (", embedding, embedding_model" if embedded else "")
                    + ") VALUES (:id, :o, :d, :v, :ord, 'text', :t, :e, 1, 1, CAST('[]' AS jsonb), "
                    "CAST('[]' AS jsonb), 8, :h" + (", CAST(:vec AS halfvec), :m" if embedded else "") + ")"
                ),
                {
                    "id": cid,
                    "o": owner_id,
                    "d": ref.document_id,
                    "v": ref.version_id,
                    "ord": i,
                    "t": body,
                    "e": emb,
                    "h": hashlib.sha256(emb.encode()).hexdigest(),
                    **(
                        {"vec": "[" + ",".join(["0.5"] * 1536) + "]", "m": get_settings().embedding_model}
                        if embedded
                        else {}
                    ),
                },
            )
            ids.append(cid)
        await s.commit()
    return ids


def db_gateway(
    tmp: pathlib.Path, sm: async_sessionmaker[AsyncSession], script: list[fake.Step] | None = None
) -> tuple[LlmGateway, fake.FakeProvider]:
    gw, prov, _, _ = make_gateway(tmp, script, ledger=DbLedgerWriter(sm))
    return gw, prov


def stage_ctx(sm: Any, job: Any, gateway: LlmGateway) -> StageContext:
    return StageContext(job, sm, {"gateway": gateway, "settings": get_settings()})
