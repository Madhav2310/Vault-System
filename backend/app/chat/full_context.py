"""Whole-document answering (design §8.2, §8.5): the pure route decision and the full-context evidence loader.

`plan_mode` is pure. `load_scope_chunks` returns every chunk of the scoped versions in
`(document, ordinal)` order, owner-scoped, as `EvidenceChunk`s (stored text, never altered).
"""

import uuid
from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.evidence import EvidenceChunk
from app.core.settings import Settings
from app.retrieval.load import load_evidence_chunks
from app.retrieval.scope import Scope

FULL_CONTEXT_TOO_LARGE = "FULL_CONTEXT_TOO_LARGE"
PROMPT_MARGIN_TOKENS = 4_000  # the full prompt may exceed the evidence budget by this much


class Plan(NamedTuple):
    mode: Literal["rag", "full_context"]
    reason: str | None


def plan_mode(
    scope: Scope,
    analysis: Any,
    requested_mode: str,
    versions_tokens: Sequence[int | None],
    settings: Settings,
) -> Plan:
    """Full context only when the user requests it and the scope fits `full_context_max_tokens` (unknown size
    counts as too large); a requested full context that is too large falls back to RAG with
    `FULL_CONTEXT_TOO_LARGE`. Retrieval is the default: automatic whole-document mode showed no gain at about
    twice the cost per turn (docs/evaluation.md)."""
    if not scope.entries or requested_mode != "full_context":
        return Plan("rag", None)
    if (
        any(t is None for t in versions_tokens)
        or sum(t or 0 for t in versions_tokens) > settings.full_context_max_tokens
    ):
        return Plan("rag", FULL_CONTEXT_TOO_LARGE)
    return Plan("full_context", None)


async def version_tokens(session: AsyncSession, owner_id: uuid.UUID, scope: Scope) -> list[int | None]:
    """`document_versions.token_count` of each scoped version, in scope order."""
    rows = await session.execute(
        text("SELECT id, token_count FROM document_versions WHERE owner_id = :o AND id = ANY(:v)"),
        {"o": owner_id, "v": list(scope.version_ids)},
    )
    by_id = {r[0]: r[1] for r in rows}
    return [by_id.get(v) for v in scope.version_ids]


async def load_scope_chunks(session: AsyncSession, owner_id: uuid.UUID, scope: Scope) -> list[EvidenceChunk]:
    """Every chunk of the scoped versions, documents in scope order and chunks by ordinal."""
    rows = await session.execute(
        text("SELECT id, version_id, ordinal FROM chunks WHERE owner_id = :o AND version_id = ANY(:v)"),
        {"o": owner_id, "v": list(scope.version_ids)},
    )
    rank = {v: i for i, v in enumerate(scope.version_ids)}
    ordered = sorted(rows, key=lambda r: (rank[r[1]], r[2]))
    return await load_evidence_chunks(session, owner_id, [r[0] for r in ordered])
