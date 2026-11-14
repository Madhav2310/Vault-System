"""`locate` node (S22.10, flag `chat_locate`): the bounded in-document loop of `app.chat.locate`.

Runs after `gate`, only when the turn is a plain question over ONE long document and the first pass is
low-confidence (the gate abstained, or the best retrieval score is below the config's `gate.high`). It adds
evidence chunks to `state.chunks`; generation, citations and checks are the normal path. A skipped or empty
loop leaves the turn exactly as the gate left it. No DB connection is held across a model call.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.gate import top_score
from app.chat.graph import TurnCtx, TurnState
from app.chat.harness import TurnLimits
from app.chat.locate import (
    LOCATE_STOPPED,
    LOCATE_USED,
    MAX_STEPS,
    SNIPPET_CHARS,
    Hit,
    LocateOut,
    View,
    assemble,
    monotonic_remaining,
    neighbour_window,
    normalise_term,
    run_loop,
)
from app.core.logging import get_logger
from app.llm.gateway import LlmGateway
from app.retrieval.load import load_evidence_chunks
from app.retrieval.retrieve import Retriever
from app.retrieval.scope import Scope

_log = get_logger("vault.chat.locate")
PROMPT: tuple[str, int] = ("locate_step", 1)
FIND_LIMIT = 8

_COLS = "id, ordinal, token_count, page_start, section_path, left(original_text, :n)"


def _hit(r: Sequence[Any]) -> Hit:
    return Hit(r[0], r[1], r[2] or 0, r[3] or 0, " > ".join(r[4] or ()), r[5] or "")


@dataclass
class PgDocStore:
    """One document version of one owner. The version is the scope entry, never a model-supplied value."""

    sm: async_sessionmaker[AsyncSession]
    retriever: Retriever
    owner_id: uuid.UUID
    scope: Scope
    run_id: uuid.UUID

    @property
    def _version(self) -> uuid.UUID:
        return self.scope.entries[0][1]

    async def _rows(self, where: str, params: dict[str, Any], tail: str = "") -> list[Hit]:
        async with self.sm() as s:
            rows = await s.execute(
                text(  # `where` and `tail` are code constants; every value is a bound parameter
                    f"SELECT {_COLS} FROM chunks WHERE owner_id = :o AND version_id = :v AND {where} {tail}"  # noqa: S608
                ),
                {"o": self.owner_id, "v": self._version, "n": SNIPPET_CHARS, **params},
            )
            return [_hit(r) for r in rows]

    async def by_ids(self, ids: Sequence[uuid.UUID]) -> list[Hit]:
        if not ids:
            return []
        by_id = {h.chunk_id: h for h in await self._rows("id = ANY(:ids)", {"ids": list(ids)})}
        return [by_id[i] for i in ids if i in by_id]

    async def owns(self, chunk_id: uuid.UUID) -> Hit | None:
        rows = await self._rows("id = :c", {"c": chunk_id})
        return rows[0] if rows else None

    async def search(self, query: str) -> list[Hit]:
        try:
            r = await self.retriever.retrieve(self.sm, self.owner_id, self.scope, query, run_id=self.run_id)
        except Exception as e:  # a failed search is an empty search; the loop goes on
            _log.warning("locate_search_failed", error_type=type(e).__name__)
            return []
        return await self.by_ids(list(r.selected))

    async def find(self, term: str) -> list[Hit]:
        return await self._rows(
            "position(:t in lower(regexp_replace(original_text, '\\s+', ' ', 'g'))) > 0",
            {"t": normalise_term(term)},
            f"ORDER BY ordinal LIMIT {FIND_LIMIT}",
        )

    async def open(self, chunk_id: uuid.UUID, k: int) -> list[Hit]:
        here = await self.owns(chunk_id)
        if here is None:
            return []
        async with self.sm() as s:
            last = (
                await s.execute(
                    text("SELECT max(ordinal) FROM chunks WHERE owner_id = :o AND version_id = :v"),
                    {"o": self.owner_id, "v": self._version},
                )
            ).scalar_one()
        lo, hi = neighbour_window(here.ordinal, k, int(last))
        return await self._rows("ordinal BETWEEN :lo AND :hi", {"lo": lo, "hi": hi}, "ORDER BY ordinal")


async def _size(store: PgDocStore) -> tuple[int, int]:
    async with store.sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT count(*), coalesce(max(page_end), 0) FROM chunks "
                    "WHERE owner_id = :o AND version_id = :v"
                ),
                {"o": store.owner_id, "v": store._version},
            )
        ).one()
    return int(row[0]), int(row[1])


def low_confidence(state: TurnState, high: float) -> bool:
    """Reuses the gate signal: abstained, or the best score is under the config's `gate.high`."""
    if state.gate_abstain:
        return True
    r = state.retrieval
    score = top_score(r.candidates) if r is not None else None
    return score is not None and score < high


def skip_reason(state: TurnState, ctx: TurnCtx) -> str | None:
    """Cheap, DB-free eligibility; None means the turn may use the loop (size is checked after)."""
    if state.compare or state.mode == "full_context" or state.route != "qa":
        return "route"
    if len(state.scope.entries) != 1:
        return "scope"
    if not state.chunks:
        return "no_first_pass"
    if not low_confidence(state, ctx.deps.retriever.config.gate.high):
        return "confident"
    return None


def is_long(chunks: int, max_page: int, min_chunks: int, min_pages: int) -> bool:
    return chunks >= min_chunks or max_page >= min_pages


def _decider(gateway: LlmGateway, state: TurnState, ctx: TurnCtx) -> Callable[[View], Awaitable[LocateOut]]:
    async def decide(view: View) -> LocateOut:
        res = await gateway.call(
            purpose="locate_step",
            lane="interactive",
            prompt=PROMPT,
            inputs={
                "question": state.analysis.standalone_query if state.analysis else state.question,
                "step": view.step,
                "steps_left": view.steps_left,
                "passages": list(view.passages),
            },
            schema=LocateOut,
            owner_id=ctx.owner_id,
            run_id=ctx.run_id,
            budget=ctx.budget,
        )
        state.call_ids += res.call_ids
        assert res.output is not None
        return res.output

    return decide


async def locate(state: TurnState, ctx: TurnCtx) -> TurnState:
    state.locate_done = True  # one pass per turn; the router never comes back
    deps = ctx.deps
    st = deps.gateway.settings
    why = skip_reason(state, ctx)
    store = (
        PgDocStore(deps.sm, deps.retriever, ctx.owner_id, state.scope, ctx.run_id) if why is None else None
    )
    if store is not None:
        n_chunks, max_page = await _size(store)
        if not is_long(n_chunks, max_page, st.chat_locate_min_chunks, st.chat_locate_min_pages):
            why = "short"
    if why is not None or store is None:
        state.last_status = "skipped"
        state.last_decision = {"skipped": why}
        return state

    # The loop may spend at most MAX_STEPS model calls on top of the route's budget: the answer keeps its own.
    lim = ctx.budget.limits
    ctx.budget.limits = TurnLimits(lim.max_llm_calls + MAX_STEPS, lim.deadline_s, lim.max_evidence_tokens)
    initial = await store.by_ids([c.chunk_id for c in state.chunks])
    res = await run_loop(
        store,
        _decider(deps.gateway, state, ctx),
        initial,
        remaining_s=monotonic_remaining(ctx.budget.deadline_at),
    )
    state.locate_log = res.log
    ids = assemble(initial, res.found, lim.max_evidence_tokens or 10**9)
    if res.found:
        async with deps.sm() as s:
            state.chunks = await load_evidence_chunks(s, ctx.owner_id, ids)
        state.gate_abstain = False  # new evidence: the model decides, as on any gated turn
        state.reasons.append(LOCATE_USED)
    if res.stop in ("invalid", "budget", "model_error", "deadline"):
        state.reasons.append(LOCATE_STOPPED)
    state.last_decision = {
        "stop": res.stop,
        "steps": len(res.log),
        "new_chunks": len(res.found),
        "evidence": len(state.chunks),
    }
    return state
