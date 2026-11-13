"""Stage implementations and registry (design §6.3).

Each stage computes with no DB connection held, then returns a `write` callback that replaces its outputs
inside the runner's fenced commit (idempotent re-runs).
"""

import asyncio
import json
import os
import pathlib
import sys
import tempfile
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.blobs import blob_key
from app.ingestion import status as vs
from app.ingestion.artifacts import extracted_key
from app.ingestion.errors import PermanentStageError, RetryableStageError, UnsupportedContentError
from app.ingestion.extract.base import ExtractedDocument
from app.ingestion.quality import assess
from app.ingestion.runner import Stage, StageContext, StageResult

BACKEND = pathlib.Path(__file__).resolve().parents[2]


async def _version(ctx: StageContext) -> Any:
    async with ctx.sm() as s:
        return (
            await s.execute(
                text("SELECT sha256, mime FROM document_versions WHERE id = :v"), {"v": ctx.job.version_id}
            )
        ).one()


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
                env={**os.environ, "VAULT_PARSE_MEMORY_MB": str(settings.parse_memory_mb)},
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=settings.parse_timeout_s)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise PermanentStageError(
                    "parse_timeout", f"extraction exceeded {settings.parse_timeout_s} s"
                ) from None
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
        await store.put(extracted_key(ctx.job.version_id), blob)
        pages = sum(1 for p in doc.pages if p.page_no is not None)
        version_id = ctx.job.version_id

        async def write(s: AsyncSession) -> None:
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
        rows, summary, needs_ocr = assess(doc)
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


def default_stages() -> dict[str, Stage]:
    return {"extract": ExtractStage(), "quality": QualityStage()}
