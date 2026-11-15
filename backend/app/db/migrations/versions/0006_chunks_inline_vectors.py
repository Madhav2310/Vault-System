"""Store `chunks.embedding` and `chunks.tsv_en` inline in the heap (STORAGE MAIN).

halfvec defaults to EXTERNAL and tsvector to EXTENDED, and a chunk row is far above the 2 kB toast threshold,
so the embedding and most `tsv_en` values lived in TOAST: the exact vector scan fetched every embedding
through the toast index (about 36,000 buffers for a 10,300-chunk owner) and the lexical leg detoasted
`tsv_en` once per call. MAIN keeps them in the heap (about 6.9 kB at most together, under the ~8.1 kB page
limit); the text, jsonb and `tsv_ids` columns move to TOAST instead. Unlike PLAIN, MAIN still
moves a value out of line as a last resort, so an oversized row can never fail an insert.

SET STORAGE only affects new tuples, so the same statement rewrites every partition: a type change with a
USING expression (a lossless halfvec -> vector -> halfvec round trip) forces a table rewrite that re-toasts
every value under the new storage, inside the migration transaction (VACUUM FULL cannot run in one). The
type change resets `embedding` to its type default, so SET STORAGE must stay in the same ALTER TABLE: its
subcommands run before the rewrite. The rewrite holds ACCESS EXCLUSIVE on `chunks` for its duration
(seconds per ~50,000 chunks). New partitions copy the parent's storage.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

REWRITE = (
    "ALTER TABLE chunks ALTER COLUMN embedding TYPE halfvec(1536) USING embedding::vector::halfvec(1536)"
)


def upgrade() -> None:
    op.execute(f"{REWRITE}, ALTER COLUMN embedding SET STORAGE MAIN, ALTER COLUMN tsv_en SET STORAGE MAIN")


def downgrade() -> None:
    op.execute(
        f"{REWRITE}, ALTER COLUMN embedding SET STORAGE EXTERNAL, ALTER COLUMN tsv_en SET STORAGE EXTENDED"
    )
