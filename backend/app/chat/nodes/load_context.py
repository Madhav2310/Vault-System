"""`load_context` node: session, scope, memory and document titles, read in one short session."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import sessions as svc
from app.chat.graph import TurnCtx, TurnState
from app.chat.memory import load_memory
from app.chat.nodes.analyze import ScopeDoc
from app.retrieval.scope import Scope, compute_scope


async def _scope_docs(s: AsyncSession, owner_id: uuid.UUID, scope: Scope) -> list[ScopeDoc]:
    rows = await s.execute(
        text("SELECT id, title FROM documents WHERE owner_id = :o AND id = ANY(:ids) AND deleted_at IS NULL"),
        {"o": owner_id, "ids": list(scope.document_ids)},
    )
    titles = {r[0]: r[1] for r in rows}
    return [ScopeDoc(d, titles[d]) for d in scope.document_ids if d in titles]


async def _supersedes(s: AsyncSession, ctx: TurnCtx) -> uuid.UUID | None:
    """The earlier run this turn replaces (Regenerate, Edit and resend), recorded on the run by the POST."""
    hints = (
        await s.execute(
            text("SELECT analysis FROM rag_runs WHERE id = :r AND owner_id = :o"),
            {"r": ctx.run_id, "o": ctx.owner_id},
        )
    ).scalar() or {}
    sup = hints.get("supersedes")
    return uuid.UUID(sup) if sup else None


async def load_context(state: TurnState, ctx: TurnCtx) -> TurnState:
    async with ctx.deps.sm() as s:
        sess = await svc.get_session(s, ctx.owner_id, ctx.session_id)
        state.scope = await compute_scope(s, ctx.owner_id, sess.document_ids)
        state.memory = await load_memory(
            s, ctx.owner_id, ctx.session_id, exclude_message_id=ctx.user_message_id
        )
        state.supersedes = await _supersedes(s, ctx)
        state.docs = await _scope_docs(s, ctx.owner_id, state.scope)
    state.last_decision = {"documents": len(state.scope.entries)}
    return state
