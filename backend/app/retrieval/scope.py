"""Retrieval scope (design 7.1): the live document versions one owner's query may touch.

The scope is computed in code from owned, non-deleted documents whose current version is ready or enriched.
Callers may narrow it, never widen it.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentVersion

SCOPE_EMPTY = "SCOPE_EMPTY"
READY_STATUSES = ("ready", "enriched")


class ScopeWideningError(ValueError):
    """A target document id lies outside the allowed scope."""


@dataclass(frozen=True)
class Scope:
    entries: tuple[tuple[uuid.UUID, uuid.UUID], ...]  # (document_id, version_id), sorted
    excluded_document_ids: tuple[uuid.UUID, ...] = ()  # requested ids that are not owned, live and ready
    empty: ClassVar["Scope"]

    @property
    def document_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(d for d, _ in self.entries)

    @property
    def version_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(v for _, v in self.entries)

    @property
    def reason(self) -> str | None:
        return SCOPE_EMPTY if not self.entries else None


Scope.empty = Scope(entries=())


def narrow(scope: Scope, target_document_ids: Sequence[uuid.UUID]) -> Scope:
    """Restrict to `target_document_ids` (a subset of the scope, else raises). Empty changes nothing."""
    if not target_document_ids:
        return scope
    targets = set(target_document_ids)
    outside = targets - set(scope.document_ids)
    if outside:
        raise ScopeWideningError(f"{len(outside)} target document(s) are outside the scope")
    return Scope(entries=tuple(e for e in scope.entries if e[0] in targets))


async def compute_scope(
    session: AsyncSession, owner_id: uuid.UUID, document_ids: Sequence[uuid.UUID] | None = None
) -> Scope:
    """Owner's live documents (all, or only `document_ids` when non-empty) at their ready current version."""
    stmt = (
        select(Document.id, DocumentVersion.id)
        .join(
            DocumentVersion,
            (DocumentVersion.id == Document.current_version_id)
            & (DocumentVersion.owner_id == Document.owner_id),
        )
        .where(
            Document.owner_id == owner_id,
            Document.deleted_at.is_(None),
            DocumentVersion.status.in_(READY_STATUSES),
        )
    )
    wanted = set(document_ids or ())
    if wanted:
        stmt = stmt.where(Document.id.in_(wanted))
    rows = (await session.execute(stmt)).all()
    entries = tuple(sorted((d, v) for d, v in rows))
    got = {d for d, _ in entries}
    return Scope(entries=entries, excluded_document_ids=tuple(sorted(wanted - got)))
