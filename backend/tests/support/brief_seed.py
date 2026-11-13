"""Seed a `ready` version with real chunks (sentence offsets included) and a job waiting at the `brief` stage,
and run that stage over a FakeProvider gateway."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import uuid7
from app.ingestion.runner import Outcome, run_job
from app.ingestion.sentences import split_sentences
from app.insights.stage import BriefStage
from app.llm.gateway import LlmGateway
from tests.support.brief_kit import settings
from tests.support.jobs import JobRef, make_job

SERVICES = (
    "This Services Agreement is made on June 1, 2020 between Medalist Diversified REIT "
    "and Acme Consulting LLC. "
    "The Consultant shall provide the Services for twelve months. "
    "The fee is $1,250,000 payable in four installments."
)
DELIVERY = "The Consultant shall deliver monthly reports. Payment is due within 30 days of each invoice."
LAW = "This Agreement is governed by the laws of the State of Delaware."


@dataclass(frozen=True)
class ChunkSpec:
    text: str
    section: tuple[str, ...] = ("Agreement",)
    tokens: int = 40


@dataclass
class Seeded:
    ref: JobRef
    chunk_ids: list[uuid.UUID] = field(default_factory=list)


def handle(text_: str, needle: str, k: int = 1) -> str:
    """Handle of the sentence of `text_` containing `needle`, when `text_` is chunk `k` of its evidence."""
    for i, (s, e) in enumerate(split_sentences(text_)):
        if needle in text_[s:e]:
            return f"E{k}.{i}"
    raise AssertionError(f"{needle!r} not in any sentence")


async def seed_ready_version(
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    chunks: Sequence[ChunkSpec],
    *,
    stage: str = "brief",
    status: str = "ready",
) -> Seeded:
    ref = await make_job(sm, owner_id, stage=stage)
    out = Seeded(ref)
    async with sm() as s:
        await s.execute(
            text("UPDATE document_versions SET status = :st WHERE id = :v"),
            {"st": status, "v": ref.version_id},
        )
        if status in ("ready", "enriched"):
            await s.execute(
                text("UPDATE documents SET current_version_id = :v WHERE id = :d"),
                {"v": ref.version_id, "d": ref.document_id},
            )
        for i, c in enumerate(chunks):
            cid = uuid7()
            out.chunk_ids.append(cid)
            await s.execute(
                text(
                    "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                    "original_text, embedding_text, page_start, page_end, section_path, sentences, spans, "
                    "token_count, content_hash) VALUES (:id, :o, :d, :v, :ord, 'text', :t, :t, :p, :p, :sp, "
                    "CAST(:sent AS jsonb), CAST('[]' AS jsonb), :tok, :h)"
                ),
                {
                    "id": cid,
                    "o": owner_id,
                    "d": ref.document_id,
                    "v": ref.version_id,
                    "ord": i,
                    "t": c.text,
                    "p": i + 1,
                    "sp": list(c.section),
                    "sent": json.dumps([list(p) for p in split_sentences(c.text)]),
                    "tok": c.tokens,
                    "h": f"h{i}",
                },
            )
        await s.commit()
    return out


async def run_brief(
    sm: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    gateway: LlmGateway | None,
    enqueue: Any,
    **settings_kw: Any,
) -> Outcome:
    services: dict[str, Any] = {"settings": settings(**settings_kw)}
    if gateway is not None:
        services["gateway"] = gateway
    return await run_job(
        sm, job_id, "w", {"brief": BriefStage()}, enqueue, order=("brief",), services=services
    )


async def insight_rows(sm: async_sessionmaker[AsyncSession], version_id: uuid.UUID) -> list[dict[str, Any]]:
    async with sm() as s:
        q = text("SELECT * FROM document_insights WHERE version_id = :v ORDER BY created_at")
        rows = await s.execute(q, {"v": version_id})
        return [dict(r) for r in rows.mappings()]
