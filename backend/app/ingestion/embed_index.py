"""Embed and index stages (design §6.3, §7.4; S1.24, S1.25).

Embed: chunks of the version without an embedding are filled from this owner's already-embedded chunks
with the same `content_hash` and model (re-uploaded versions share most chunks); the rest are embedded in
batches of at most 128 through the gateway (lane `background`, ledger rows carry `job_id`). Every batch is
persisted as soon as it returns, so a retry after a provider failure never pays for finished batches again.

Index: the version becomes `ready`, `documents.current_version_id` moves to it, and the previously current
version is marked `superseded`. All of it happens inside the runner's fenced commit.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion import status as vs
from app.ingestion.errors import PermanentStageError, RetryableStageError
from app.ingestion.runner import StageContext, StageResult
from app.ingestion.stages import document_deleted
from app.insights.staleness import on_version_ready
from app.llm.errors import BadRequestError, LlmError
from app.llm.gateway import MAX_EMBED_BATCH

EMBED_BATCH = MAX_EMBED_BATCH


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def _persist(
    sm: Any, owner_id: uuid.UUID, version_id: uuid.UUID, model: str, rows: list[tuple[uuid.UUID, list[float]]]
) -> None:
    if not rows:
        return
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE chunks SET embedding = CAST(:e AS halfvec), embedding_model = :m "
                "WHERE owner_id = :o AND version_id = :v AND id = :id"
            ),
            [{"e": vec_literal(v), "m": model, "o": owner_id, "v": version_id, "id": cid} for cid, v in rows],
        )
        await s.commit()


class EmbedStage:
    name = "embed"

    async def run(self, ctx: StageContext) -> StageResult:
        gateway, settings = ctx.services["gateway"], ctx.services["settings"]
        job = ctx.job
        if await document_deleted(ctx):
            return (
                StageResult()
            )  # no paid calls for a document deleted meanwhile; the commit fence aborts the job
        model: str = settings.embedding_model
        async with ctx.sm() as s:
            todo = (
                await s.execute(
                    text(
                        "SELECT id, content_hash, embedding_text FROM chunks "
                        "WHERE owner_id = :o AND version_id = :v "
                        "AND (embedding IS NULL OR embedding_model <> :m) "
                        "ORDER BY ordinal"
                    ),
                    {"o": job.owner_id, "v": job.version_id, "m": model},
                )
            ).all()
            total = (
                await s.execute(
                    text("SELECT count(*) FROM chunks WHERE owner_id = :o AND version_id = :v"),
                    {"o": job.owner_id, "v": job.version_id},
                )
            ).scalar_one()
        reused = 0
        pending = list(todo)
        if pending:
            async with ctx.sm() as s:
                # same owner, same content hash and model: copy the vector instead of paying again
                res = await s.execute(
                    text(
                        "UPDATE chunks c SET embedding = src.embedding, "
                        "embedding_model = src.embedding_model "
                        "FROM (SELECT DISTINCT ON (content_hash) content_hash, embedding, embedding_model "
                        "      FROM chunks WHERE owner_id = :o AND embedding IS NOT NULL "
                        "      AND embedding_model = :m "
                        "      AND version_id <> :v ORDER BY content_hash, created_at DESC) src "
                        "WHERE c.owner_id = :o AND c.version_id = :v AND c.content_hash = src.content_hash "
                        "AND (c.embedding IS NULL OR c.embedding_model <> :m)"
                    ),
                    {"o": job.owner_id, "v": job.version_id, "m": model},
                )
                reused = int(res.rowcount or 0)  # type: ignore[attr-defined]
                await s.commit()
                still = (
                    await s.execute(
                        text(
                            "SELECT id, embedding_text FROM chunks WHERE owner_id = :o AND version_id = :v "
                            "AND (embedding IS NULL OR embedding_model <> :m) ORDER BY ordinal"
                        ),
                        {"o": job.owner_id, "v": job.version_id, "m": model},
                    )
                ).all()
            pending = list(still)
        calls = 0
        for i in range(0, len(pending), EMBED_BATCH):
            batch = pending[i : i + EMBED_BATCH]
            try:
                out = await gateway.embed(
                    purpose="embed",
                    lane="background",
                    texts=[r.embedding_text for r in batch],
                    owner_id=job.owner_id,
                    job_id=job.id,
                )
            except BadRequestError as e:  # the input itself is rejected: retrying cannot help
                raise PermanentStageError("embedding_rejected", e.error_type) from None
            except LlmError as e:
                raise RetryableStageError(e.error_type, "embedding provider failed") from None
            calls += 1
            await _persist(
                ctx.sm,
                job.owner_id,
                job.version_id,
                model,
                [(r.id, v) for r, v in zip(batch, out.vectors, strict=True)],
            )
        detail = {"chunks": total, "embedded": len(pending), "reused": reused, "calls": calls, "model": model}
        return StageResult(detail=detail)


class IndexStage:
    name = "index"

    async def run(self, ctx: StageContext) -> StageResult:
        job = ctx.job
        async with ctx.sm() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT count(*) AS n, count(*) FILTER (WHERE embedding IS NULL) AS missing "
                        "FROM chunks WHERE owner_id = :o AND version_id = :v"
                    ),
                    {"o": job.owner_id, "v": job.version_id},
                )
            ).one()
        if row.n == 0:
            raise PermanentStageError("no_text", "the document produced no searchable text")
        if row.missing:
            raise RetryableStageError("embeddings_missing", f"{row.missing} chunks without an embedding")

        async def write(s: AsyncSession) -> None:
            cur = (
                await s.execute(
                    text("SELECT status, document_id FROM document_versions WHERE id = :v FOR UPDATE"),
                    {"v": job.version_id},
                )
            ).one()
            if cur.status in (vs.READY, vs.ENRICHED, vs.SUPERSEDED):
                return  # re-delivery after this stage committed: nothing to redo, hook not called again
            doc = (
                await s.execute(
                    text("SELECT current_version_id FROM documents WHERE id = :d FOR UPDATE"),
                    {"d": cur.document_id},
                )
            ).one()
            prev = doc.current_version_id
            if prev is not None and prev != job.version_id:
                await s.execute(
                    text(
                        "UPDATE document_versions SET status = 'superseded', superseded_at = now() "
                        "WHERE id = :p AND status IN ('ready', 'enriched')"
                    ),
                    {"p": prev},
                )
            await s.execute(
                text("UPDATE documents SET current_version_id = :v WHERE id = :d"),
                {"v": job.version_id, "d": cur.document_id},
            )
            await on_version_ready(s, job.owner_id, cur.document_id, job.version_id)

        return StageResult(write=write, terminal_status=vs.READY, detail={"chunks": row.n})
