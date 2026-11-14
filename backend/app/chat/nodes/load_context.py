"""`load_context` node: session, scope, memory and document titles, read in one short session."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat import sessions as svc
from app.chat.graph import TurnCtx, TurnState
from app.chat.memory import load_memory
from app.chat.named_docs import LEAD_CHARS, named_on
from app.chat.nodes._common import TARGET_IGNORED
from app.chat.nodes.analyze import ScopeDoc
from app.retrieval.scope import Scope, ScopeWideningError, compute_scope, narrow


async def _scope_docs(
    s: AsyncSession, owner_id: uuid.UUID, scope: Scope, *, with_lead: bool = False
) -> list[ScopeDoc]:
    rows = await s.execute(
        text("SELECT id, title FROM documents WHERE owner_id = :o AND id = ANY(:ids) AND deleted_at IS NULL"),
        {"o": owner_id, "ids": list(scope.document_ids)},
    )
    titles = {r[0]: r[1] for r in rows}
    leads: dict[uuid.UUID, str] = {}
    if with_lead:  # opening text names documents whose stored title is a file stem (named-document retrieval)
        opening = await s.execute(
            text(
                "SELECT d.id, left(c.original_text, :n) FROM documents d "
                "JOIN chunks c ON c.owner_id = d.owner_id "
                "AND c.version_id = d.current_version_id AND c.ordinal = 0 "
                "WHERE d.owner_id = :o AND d.id = ANY(:ids) AND d.deleted_at IS NULL"
            ),
            {"o": owner_id, "ids": list(scope.document_ids), "n": LEAD_CHARS},
        )
        leads = {r[0]: r[1] or "" for r in opening}
    return [ScopeDoc(d, titles[d], leads.get(d, "")) for d in scope.document_ids if d in titles]


async def _clarify_state(s: AsyncSession, ctx: TurnCtx) -> tuple[bool, uuid.UUID | None, uuid.UUID | None]:
    """(the last assistant message was a clarification, the document chosen from it, the run it replaces)."""
    last = (
        await s.execute(
            text(
                "SELECT response_type FROM chat_messages WHERE session_id = :s AND owner_id = :o "
                "AND role = 'assistant' ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"s": ctx.session_id, "o": ctx.owner_id},
        )
    ).scalar()
    hints = (
        await s.execute(
            text("SELECT analysis FROM rag_runs WHERE id = :r AND owner_id = :o"),
            {"r": ctx.run_id, "o": ctx.owner_id},
        )
    ).scalar() or {}
    pick, sup = hints.get("clarify_pick", {}).get("document_id"), hints.get("supersedes")
    return last == "clarify", uuid.UUID(pick) if pick else None, uuid.UUID(sup) if sup else None


async def load_context(state: TurnState, ctx: TurnCtx) -> TurnState:
    async with ctx.deps.sm() as s:
        sess = await svc.get_session(s, ctx.owner_id, ctx.session_id)
        state.scope = await compute_scope(s, ctx.owner_id, sess.document_ids)
        state.memory = await load_memory(
            s, ctx.owner_id, ctx.session_id, exclude_message_id=ctx.user_message_id
        )
        state.prev_clarified, state.pick_document_id, state.supersedes = await _clarify_state(s, ctx)
        if state.pick_document_id is not None:  # the chosen option narrows the scope (never widens it)
            try:
                state.scope = narrow(state.scope, [state.pick_document_id])
            except ScopeWideningError:
                state.reasons.append(TARGET_IGNORED)
        state.docs = await _scope_docs(
            s, ctx.owner_id, state.scope, with_lead=named_on(ctx.deps.gateway.settings)
        )
    state.last_decision = {"documents": len(state.scope.entries)}
    return state
