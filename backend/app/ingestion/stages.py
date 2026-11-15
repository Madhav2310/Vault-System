"""Stage implementations and registry.

Each stage computes with no DB connection held, then returns a `write` callback that replaces its outputs
inside the runner's fenced commit (idempotent re-runs).
"""

import asyncio
import json
import os
import pathlib
import sys
import tempfile
from dataclasses import asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.documents.blobs import blob_key
from app.ingestion import status as vs
from app.ingestion.artifacts import extracted_key
from app.ingestion.chunk import CHUNKER_VERSION, chunk_document
from app.ingestion.errors import PermanentStageError, RetryableStageError, UnsupportedContentError
from app.ingestion.extract.base import ExtractedDocument
from app.ingestion.quality import assess
from app.ingestion.runner import Stage, StageContext, StageResult

BACKEND = pathlib.Path(__file__).resolve().parents[2]


async def _version(ctx: StageContext) -> Any:
    async with ctx.sm() as s:
        return (
            await s.execute(
                text("SELECT sha256, mime FROM document_versions WHERE id = :v AND owner_id = :o"),
                {"v": ctx.job.version_id, "o": ctx.job.owner_id},
            )
        ).one()


async def document_deleted(ctx: StageContext) -> bool:
    async with ctx.sm() as s:
        return bool(
            (
                await s.execute(
                    text(
                        "SELECT d.deleted_at IS NOT NULL FROM documents d "
                        "JOIN document_versions v ON v.document_id = d.id "
                        "WHERE v.id = :v AND v.owner_id = :o"
                    ),
                    {"v": ctx.job.version_id, "o": ctx.job.owner_id},
                )
            ).scalar_one_or_none()
        )


async def version_not_current(ctx: StageContext) -> bool:
    """True when the job's version is not its document's current version (an older version that finished
    after a newer one stays behind it): enrichment of it would never be read."""
    async with ctx.sm() as s:
        return bool(
            (
                await s.execute(
                    text(
                        "SELECT d.current_version_id IS DISTINCT FROM v.id FROM documents d "
                        "JOIN document_versions v ON v.document_id = d.id "
                        "WHERE v.id = :v AND v.owner_id = :o"
                    ),
                    {"v": ctx.job.version_id, "o": ctx.job.owner_id},
                )
            ).scalar_one_or_none()
        )


# Environment of the parser subprocess: it reads untrusted files, so it gets no API keys or DB credentials.
_PARSER_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONHASHSEED")


async def load_extracted(ctx: StageContext) -> ExtractedDocument:
    data = await ctx.services["store"].get(extracted_key(ctx.job.version_id))
    return await asyncio.to_thread(ExtractedDocument.from_json_gz, data)


class ExtractStage:
    """Parse the blob in a subprocess (killable on timeout, memory-capped) and store the ExtractedDocument."""

    name = "extract"

    async def run(self, ctx: StageContext) -> StageResult:
        store, settings = ctx.services["store"], ctx.services["settings"]
        v = await _version(ctx)
        with tempfile.TemporaryDirectory(prefix="extract-") as d:
            src, out = pathlib.Path(d) / "input", pathlib.Path(d) / "out.json.gz"
            data = await store.get(blob_key(v.sha256))
            await asyncio.to_thread(src.write_bytes, data)
            del data
            env = {k: os.environ[k] for k in _PARSER_ENV_KEYS if k in os.environ}
            env["VAULT_PARSE_MEMORY_MB"] = str(settings.parse_memory_mb)
            env["VAULT_MAX_PAGES"] = str(settings.max_pages)
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "app.ingestion.extract.worker",
                str(src),
                v.mime,
                str(out),
                cwd=str(BACKEND),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=settings.parse_timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise PermanentStageError(
                    "parse_timeout", f"extraction exceeded {settings.parse_timeout_s} s"
                ) from None
            except asyncio.CancelledError:  # lease lost or shutdown: never leave a parser running
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode == 3:
                info = json.loads(stdout.decode().strip().splitlines()[-1])
                cls = UnsupportedContentError if info["status"] == vs.UNSUPPORTED else PermanentStageError
                raise cls(info["code"], info["detail"])
            if proc.returncode != 0:
                raise RetryableStageError(
                    "extract_crashed", f"extraction process exited with {proc.returncode}"
                )
            blob = await asyncio.to_thread(out.read_bytes)
        doc = await asyncio.to_thread(ExtractedDocument.from_json_gz, blob)
        pages = sum(1 for p in doc.pages if p.page_no is not None)
        version_id = ctx.job.version_id

        async def write(s: AsyncSession) -> None:
            # Inside the fenced commit: a worker that lost its lease, or a document deleted meanwhile, never
            # gets to (re)create the artifact that a purge would then have to chase.
            await store.put(extracted_key(version_id), blob)
            await s.execute(
                text("UPDATE document_versions SET page_count = :n WHERE id = :v"),
                {"n": pages or None, "v": version_id},
            )

        return StageResult(
            write=write, detail={"pages": pages, "elements": len(doc.elements), "extractor": doc.extractor}
        )


class QualityStage:
    """`pages` rows with quality scores and flags; ends the pipeline as `needs_ocr` for scanned documents."""

    name = "quality"

    async def run(self, ctx: StageContext) -> StageResult:
        doc = await load_extracted(ctx)
        rows, summary, needs_ocr = await asyncio.to_thread(assess, doc)
        version_id = ctx.job.version_id

        async def write(s: AsyncSession) -> None:
            await s.execute(text("DELETE FROM pages WHERE version_id = :v"), {"v": version_id})
            for r in rows:
                await s.execute(
                    text(
                        "INSERT INTO pages (version_id, page_no, width, height, text_chars, quality_score, "
                        "flags) VALUES (:v, :p, :w, :h, :c, :q, :f)"
                    ),
                    {
                        "v": version_id,
                        "p": r.page_no,
                        "w": r.width,
                        "h": r.height,
                        "c": r.text_chars,
                        "q": r.quality_score,
                        "f": r.flags,
                    },
                )
            await s.execute(
                text("UPDATE document_versions SET quality = CAST(:q AS jsonb) WHERE id = :v"),
                {"q": json.dumps(summary), "v": version_id},
            )

        return StageResult(
            write=write,
            terminal_status=vs.NEEDS_OCR if needs_ocr else None,
            detail={"pages": len(rows), "needs_ocr_pages": summary["needs_ocr_pages"]},
        )


class ChunkStage:
    """`chunks` rows (no embeddings yet) from the extracted document."""

    name = "chunk"

    async def run(self, ctx: StageContext) -> StageResult:
        doc = await load_extracted(ctx)
        async with ctx.sm() as s:
            title = (
                await s.execute(
                    text(
                        "SELECT d.title, d.id FROM documents d "
                        "JOIN document_versions v ON v.document_id = d.id "
                        "WHERE v.id = :v AND v.owner_id = :o"
                    ),
                    {"v": ctx.job.version_id, "o": ctx.job.owner_id},
                )
            ).one()
        drafts = await asyncio.to_thread(chunk_document, doc, title.title)
        job = ctx.job
        rows = [
            {
                "id": uuid7(),
                "owner_id": job.owner_id,
                "document_id": title.id,
                "version_id": job.version_id,
                "ordinal": c.ordinal,
                "kind": c.kind,
                "original_text": c.original_text,
                "structural_prefix": c.structural_prefix,
                "embedding_text": c.embedding_text,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "section_path": c.section_path,
                "sentences": json.dumps(c.sentences),
                "spans": json.dumps([asdict(sp) for sp in c.spans]),
                "token_count": c.token_count,
                "content_hash": c.content_hash,
            }
            for c in drafts
        ]
        tokens = sum(c.token_count for c in drafts)

        async def write(s: AsyncSession) -> None:
            await s.execute(
                text("DELETE FROM chunks WHERE owner_id = :o AND version_id = :v"),
                {"o": job.owner_id, "v": job.version_id},
            )
            if rows:
                await s.execute(
                    text(
                        "INSERT INTO chunks (id, owner_id, document_id, version_id, ordinal, kind, "
                        "original_text, structural_prefix, embedding_text, page_start, page_end, "
                        "section_path, sentences, spans, token_count, content_hash) VALUES (:id, "
                        ":owner_id, :document_id, :version_id, :ordinal, :kind, :original_text, "
                        ":structural_prefix, :embedding_text, :page_start, :page_end, :section_path, "
                        "CAST(:sentences AS jsonb), CAST(:spans AS jsonb), :token_count, :content_hash)"
                    ),
                    rows,
                )
            await s.execute(
                text("UPDATE document_versions SET chunker_version = :cv, token_count = :t WHERE id = :v"),
                {"cv": CHUNKER_VERSION, "t": tokens, "v": job.version_id},
            )

        return StageResult(write=write, detail={"chunks": len(rows), "tokens": tokens})


def default_stages() -> dict[str, Stage]:
    from app.ingestion.embed_index import EmbedStage, IndexStage
    from app.insights.stage import BriefStage

    return {
        "extract": ExtractStage(),
        "quality": QualityStage(),
        "chunk": ChunkStage(),
        "embed": EmbedStage(),
        "index": IndexStage(),
        "brief": BriefStage(),
    }
