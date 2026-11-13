"""Identifier tokens at index time (design §6.5, §7.3; S17.04).

`chunks.tsv_ids = to_tsvector('simple', <tokens joined by space>)` over the identifier-like tokens of
`original_text` (section numbers, clause refs, amounts, percentages, dates, codes). The tokens come from the
same regex as `lexical.identifier_query`, so an identifier in a query matches the identifier in the chunk.
Chunks without identifiers keep `tsv_ids` NULL. `original_text` is read, never changed.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.lexical import identifier_query

BACKFILL_BATCH = 500


def extract_identifier_tokens(original_text: str) -> list[str]:
    """Distinct identifier tokens in order of first appearance."""
    return list(dict.fromkeys(identifier_query(original_text).split()))


def tsv_ids_source(original_text: str) -> str | None:
    """The string fed to `to_tsvector('simple', ...)`, or None when the chunk has no identifiers."""
    tokens = extract_identifier_tokens(original_text)
    return " ".join(tokens) if tokens else None


_SET = (
    "UPDATE chunks SET tsv_ids = CASE WHEN CAST(:src AS text) IS NULL THEN NULL "
    "ELSE to_tsvector('simple', CAST(:src AS text)) END WHERE id = :id AND owner_id = :o"
)


async def fill_tsv_ids(s: AsyncSession, owner_id: uuid.UUID, version_id: uuid.UUID) -> int:
    """Set `tsv_ids` on every chunk of the version (idempotent); returns the chunks that have identifiers."""
    rows = (
        await s.execute(
            text("SELECT id, original_text FROM chunks WHERE owner_id = :o AND version_id = :v"),
            {"o": owner_id, "v": version_id},
        )
    ).all()
    params = [{"id": r.id, "o": owner_id, "src": tsv_ids_source(r.original_text)} for r in rows]
    if params:
        await s.execute(text(_SET), params)
    return sum(p["src"] is not None for p in params)


async def backfill_tsv_ids(
    s: AsyncSession, batch: int = BACKFILL_BATCH, owner_id: uuid.UUID | None = None
) -> int:
    """Fill `tsv_ids` for existing chunks that have identifiers and no `tsv_ids` yet; returns rows updated.

    Chunks without identifiers stay NULL, so a second run updates 0 rows. `owner_id` limits it to one owner."""
    updated = 0
    after: uuid.UUID | None = None
    while True:
        rows = (
            await s.execute(
                text(
                    "SELECT id, owner_id, original_text FROM chunks WHERE tsv_ids IS NULL "
                    "AND (CAST(:after AS uuid) IS NULL OR id > :after) "
                    "AND (CAST(:o AS uuid) IS NULL OR owner_id = :o) ORDER BY id LIMIT :n"
                ),
                {"after": after, "n": batch, "o": owner_id},
            )
        ).all()
        if not rows:
            return updated
        after = rows[-1][0]
        params = [
            {"id": cid, "o": owner, "src": src}
            for cid, owner, body in rows
            if (src := tsv_ids_source(body)) is not None
        ]
        if params:
            await s.execute(text(_SET), params)
            updated += len(params)
        await s.commit()
