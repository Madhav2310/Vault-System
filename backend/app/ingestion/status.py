"""Version status machine. Stage status transitions go through `set_version_status`, which only
moves along a legal edge (compare-and-set on the current status)."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UPLOADED, PROCESSING, READY, ENRICHED = "uploaded", "processing", "ready", "enriched"
NEEDS_OCR, UNSUPPORTED, FAILED, SUPERSEDED = "needs_ocr", "unsupported", "failed", "superseded"

TRANSITIONS: dict[str, frozenset[str]] = {
    UPLOADED: frozenset({PROCESSING}),
    PROCESSING: frozenset({READY, NEEDS_OCR, UNSUPPORTED, FAILED}),
    READY: frozenset({ENRICHED, SUPERSEDED}),
    ENRICHED: frozenset({SUPERSEDED}),
    FAILED: frozenset({PROCESSING}),  # the user's retry action
    NEEDS_OCR: frozenset(),
    UNSUPPORTED: frozenset(),
    SUPERSEDED: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    pass


def check_transition(current: str, to: str) -> None:
    if to not in TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(f"version status {current} -> {to} is not allowed")


async def set_version_status(
    session: AsyncSession,
    version_id: uuid.UUID,
    to: str,
    *,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> bool:
    """CAS along a legal edge. Returns False when the version already has status `to` (idempotent re-run);
    raises IllegalTransitionError for any other illegal move."""
    sources = [s for s, targets in TRANSITIONS.items() if to in targets]
    row = (
        await session.execute(
            text(
                "UPDATE document_versions SET status = :to, error_code = :ec, error_detail = :ed, "
                "ready_at = CASE WHEN :to = 'ready' THEN now() ELSE ready_at END, "
                "enriched_at = CASE WHEN :to = 'enriched' THEN now() ELSE enriched_at END "
                "WHERE id = :v AND status = ANY(:src) RETURNING id"
            ),
            {"to": to, "ec": error_code, "ed": error_detail, "v": version_id, "src": sources},
        )
    ).first()
    if row is not None:
        return True
    current = (
        await session.execute(text("SELECT status FROM document_versions WHERE id = :v"), {"v": version_id})
    ).scalar_one()
    if current == to:
        return False
    raise IllegalTransitionError(f"version status {current} -> {to} is not allowed")
