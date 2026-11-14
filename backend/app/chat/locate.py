"""Routed in-document locate loop for long filings.

A bounded, code-owned loop inside ONE scoped document. The model returns a typed next move (`search`, `find`,
`open`, `done`); code validates it, runs it and accumulates evidence chunks. An unknown action, a malformed
argument or a chunk outside the document ends the loop; so do the step cap, the turn budget and a model error.
Nothing here raises to the turn: the caller keeps the first-pass evidence when the loop finds nothing.

The log (`rag_runs.analysis.locate`) holds `{step, action, arg_digest, new_chunks}` only: digests and counts,
never document text or the model's query.
"""

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from app.llm.errors import BudgetExceededError, LlmError

MAX_STEPS = 4
MAX_OPEN_K = 2
MAX_ARG_CHARS = 200
MIN_TERM_CHARS = 2
SEARCH_TAKE = 5  # chunks kept from one `search`
FIND_TAKE = 5  # chunks kept from one `find`
SNIPPET_CHARS = 240
MAX_VIEW_PASSAGES = 20
MIN_REMAINING_S = 8.0  # leave this much of the turn deadline for the answer

LOCATE_USED = "LOCATE_USED"
LOCATE_STOPPED = "LOCATE_STOPPED"

ACTIONS = ("search", "find", "open", "done")
Stop = Literal["done", "max_steps", "invalid", "budget", "model_error", "deadline", "none"]


class LocateOut(BaseModel):
    """Strict-mode compatible: every field required, `T | None` only."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["search", "find", "open", "done"]
    query: str | None
    chunk: str | None
    k: int | None


@dataclass(frozen=True)
class Hit:
    chunk_id: uuid.UUID
    ordinal: int
    token_count: int
    page: int
    section: str
    snippet: str = ""


class DocStore(Protocol):
    """Reads inside one document version. Every method is owner- and version-scoped."""

    async def search(self, query: str) -> list[Hit]: ...
    async def find(self, term: str) -> list[Hit]: ...
    async def open(self, chunk_id: uuid.UUID, k: int) -> list[Hit]: ...
    async def owns(self, chunk_id: uuid.UUID) -> Hit | None: ...


@dataclass(frozen=True)
class View:
    step: int
    steps_left: int
    passages: tuple[dict[str, Any], ...]


Decider = Callable[[View], Awaitable[LocateOut]]


@dataclass
class LoopResult:
    found: list[Hit] = field(default_factory=list)  # new chunks only, in discovery order
    log: list[dict[str, Any]] = field(default_factory=list)
    stop: Stop = "none"


def digest(action: str, arg: str | None) -> str:
    return hashlib.sha256(f"{action}\x00{arg or ''}".encode()).hexdigest()[:12]


def neighbour_window(ordinal: int, k: int, last_ordinal: int) -> tuple[int, int]:
    """Inclusive ordinal range of `open(chunk, +-k)`, clipped to the document (ordinals 0..last_ordinal)."""
    return max(0, ordinal - k), min(last_ordinal, ordinal + k)


def normalise_term(term: str) -> str:
    return " ".join(term.lower().split())


@dataclass(frozen=True)
class Action:
    kind: Literal["search", "find", "open", "done"]
    arg: str | None = None
    chunk_id: uuid.UUID | None = None
    k: int = 0


async def validate(out: LocateOut, refs: dict[str, uuid.UUID], store: DocStore) -> Action | None:
    """The typed action, or None when the move is unknown, malformed or points outside the document."""
    if out.action not in ACTIONS:
        return None
    if out.action == "done":
        return Action("done")
    if out.action in ("search", "find"):
        arg = " ".join((out.query or "").split())
        if len(arg) > MAX_ARG_CHARS or len(normalise_term(arg)) < MIN_TERM_CHARS:
            return None
        return Action(out.action, arg)
    k = out.k
    if k is None or isinstance(k, bool) or not 1 <= k <= MAX_OPEN_K or not out.chunk:
        return None
    ref = out.chunk.strip()
    chunk_id = refs.get(ref)
    if chunk_id is None:
        try:
            cand = uuid.UUID(ref)
        except ValueError:
            return None
        if await store.owns(cand) is None:  # a chunk id that is not in this document
            return None
        chunk_id = cand
    return Action("open", ref, chunk_id, k)


async def run_loop(
    store: DocStore,
    decide: Decider,
    initial: Sequence[Hit],
    *,
    max_steps: int = MAX_STEPS,
    remaining_s: Callable[[], float] = lambda: float("inf"),
) -> LoopResult:
    max_steps = min(max_steps, MAX_STEPS)
    res = LoopResult()
    refs: dict[str, uuid.UUID] = {}
    seen: dict[uuid.UUID, Hit] = {}

    def see(hits: Sequence[Hit], new_only: bool) -> int:
        new = 0
        for h in hits:
            if h.chunk_id in seen:
                continue
            seen[h.chunk_id] = h
            refs[f"C{len(refs) + 1}"] = h.chunk_id
            if new_only:
                new += 1
                res.found.append(h)
        return new

    see(initial, new_only=False)  # first-pass chunks are known, not new

    for step in range(1, max_steps + 1):
        if remaining_s() < MIN_REMAINING_S:
            res.stop = "deadline"
            return res
        view = View(
            step,
            max_steps - step + 1,
            tuple(
                {"ref": r, "page": seen[i].page, "section": seen[i].section, "text": seen[i].snippet}
                for r, i in list(refs.items())[-MAX_VIEW_PASSAGES:]
            ),
        )
        try:
            out = await decide(view)
        except BudgetExceededError:
            res.stop = "budget"
            return res
        except LlmError:
            res.stop = "model_error"
            return res
        action = await validate(out, refs, store)
        if action is None:
            res.log.append(
                {
                    "step": step,
                    "action": "invalid",
                    "arg_digest": digest("invalid", str(out.action)),
                    "new_chunks": 0,
                }
            )
            res.stop = "invalid"
            return res
        new = 0
        if action.kind == "search" and action.arg:
            new = see((await store.search(action.arg))[:SEARCH_TAKE], True)
        elif action.kind == "find" and action.arg:
            new = see((await store.find(action.arg))[:FIND_TAKE], True)
        elif action.kind == "open" and action.chunk_id is not None:
            new = see(await store.open(action.chunk_id, action.k), True)
        res.log.append(
            {
                "step": step,
                "action": action.kind,
                "arg_digest": digest(action.kind, action.arg),
                "new_chunks": new,
            }
        )
        if action.kind == "done":
            res.stop = "done"
            return res
    res.stop = "max_steps"
    return res


def assemble(initial: Sequence[Hit], found: Sequence[Hit], token_cap: int) -> list[uuid.UUID]:
    """Evidence ids: loop finds first (they were chosen for the question), then the first pass, deduplicated
    and cut at `token_cap` tokens (the first chunk is always kept)."""
    out: list[uuid.UUID] = []
    total = 0
    for h in [*found, *initial]:
        if h.chunk_id in out:
            continue
        if out and total + h.token_count > token_cap:
            continue
        out.append(h.chunk_id)
        total += h.token_count
    return out


def monotonic_remaining(deadline_at: float) -> Callable[[], float]:
    return lambda: deadline_at - time.monotonic()
