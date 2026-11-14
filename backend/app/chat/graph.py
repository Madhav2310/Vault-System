"""Explicit turn graph: typed state, async nodes, conditional edges, a node runner.

Small and dependency-free on purpose (no LangChain/LangGraph): the code, not the model, picks the next node.
The runner wraps every node with an OTel span `vault.node.<name>`, the turn deadline, and an automatic
`StepRecord`. A node that raises or times out becomes a failed step and the turn jumps to the `on_error` node
(persist), which writes the error response: a turn never ends stuck. At most `MAX_NODE_RUNS` executions run.
`describe()` returns a Mermaid flowchart so the turn diagram is generated from code.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.events import NODE_STAGE, EventSink, NullSink, Status, TurnEvent
from app.chat.harness import TurnBudget
from app.chat.ledger import StepRecord
from app.core.logging import get_logger
from app.llm.gateway import LlmGateway
from app.retrieval.query_embed import QueryEmbedding
from app.retrieval.retrieve import RetrievalResult, Retriever
from app.retrieval.scope import Scope

if TYPE_CHECKING:  # node modules import this module; keep the runtime import graph acyclic
    from app.chat.clarify import Clarification
    from app.chat.nodes.analyze import AnalyzeResult, ScopeDoc
    from app.chat.nodes.generate import GenerateResult
    from app.chat.speculative import SpeculativeRetrieval

_log = get_logger("vault.chat.graph")
_tracer = trace.get_tracer("app.chat.graph")

END = "__end__"
MAX_NODE_RUNS = 12
TURN_ERROR = "TURN_ERROR"


@dataclass(frozen=True)
class TurnDeps:
    sm: async_sessionmaker[AsyncSession]
    gateway: LlmGateway
    retriever: Retriever


@dataclass
class TurnCtx:
    """Per-turn constants plus the mutable budget (replaced once the route is known)."""

    deps: TurnDeps
    owner_id: uuid.UUID
    session_id: uuid.UUID
    run_id: uuid.UUID
    user_message_id: uuid.UUID
    budget: TurnBudget
    t_start: float = field(default_factory=time.monotonic)
    sink: EventSink = field(default_factory=NullSink)
    stage: str | None = None  # last `status` stage emitted
    segments_emitted: int = 0  # `segment` events sent so far (the index of the next one)
    ttft_ms: int | None = None  # turn start to the first model-written segment; None without one

    def mark_first_segment(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = max(1, int((time.monotonic() - self.t_start) * 1000))

    async def emit(self, event: TurnEvent) -> None:
        """Send an event to the sink; a sink failure never fails the turn."""
        try:
            await self.sink.emit(event)
        except Exception as e:
            _log.warning("event_emit_failed", error_type=type(e).__name__)

    async def enter(self, node: str) -> None:
        """Emit `status` when `node` moves the turn to a new user-visible stage."""
        stage = NODE_STAGE.get(node)
        if stage is not None and stage != self.stage:
            self.stage = stage
            await self.emit(Status(run_id=str(self.run_id), stage=stage))


@dataclass
class TurnState:
    question: str
    mode: str = "auto"
    memory: Any = None
    docs: list[ScopeDoc] = field(default_factory=list)
    scope: Scope = Scope.empty
    analysis: AnalyzeResult | None = None
    embedding: QueryEmbedding | None = None  # speculative embedding of the raw question
    spec_retrieval: SpeculativeRetrieval | None = None  # retrieval of the raw question, run during analyze
    retrieval: RetrievalResult | None = None
    chunks: list[Any] = field(default_factory=list)
    evidence: Any = None
    generation: GenerateResult | None = None
    verdicts: dict[str, Any] = field(default_factory=dict)
    persisted: Any = None  # PersistedTurn, set by the persist node
    reasons: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    call_ids: list[uuid.UUID] = field(default_factory=list)
    route: str = "qa"
    status: str = "completed"
    failure: tuple[str, ...] | None = None  # reason codes of a failed turn; persist writes the error response
    steps: list[StepRecord] = field(default_factory=list)
    last_decision: dict[str, Any] | None = None  # set by a node; copied into its step
    last_status: str = "ok"  # a node may report "failed" for a handled failure
    last_error: str | None = None
    last_reason: str | None = None  # a node may set a reason code for its step (e.g. FAST_PATH)
    fast_path: bool = False  # set by `route_question`: `analyze` is skipped
    gate_abstain: bool = False  # set by `gate`: the evidence is below the floor
    abstain_features: dict[str, Any] | None = None  # set by `verify` (flag `chat_abstain_scored`)
    compare: bool = False  # set by `analyze`: a compare turn over 2-5 documents
    prev_clarified: bool = False  # set by `load_context`: the last assistant message was a clarification
    pick_document_id: uuid.UUID | None = None  # set by `load_context`: the option chosen from a clarification
    supersedes: uuid.UUID | None = None  # set by `load_context`: the earlier run this turn replaces
    clarify_hint: bool = (
        False  # set by `analyze`: the model hinted at a document ambiguity (code still decides)
    )
    clarification: Clarification | None = None  # set by a code rule; sends the turn to `clarify`
    locate_on: bool = False  # flag `chat_locate`: `gate` may route to `locate`; set once by the runner
    locate_done: bool = False  # set by `locate`: it runs at most once per turn
    locate_log: list[dict[str, Any]] = field(default_factory=list)  # {step, action, arg_digest, new_chunks}

    def fail(self, *codes: str | None) -> None:
        self.status = "failed"
        self.failure = tuple(c for c in codes if c)


Node = Callable[[TurnState, TurnCtx], Awaitable[TurnState]]
Router = Callable[[TurnState], str]


def router(*targets: str) -> Callable[[Router], Router]:
    """Declare the possible targets of a router (used by `describe()` and checked at run time)."""

    def deco(fn: Router) -> Router:
        fn.targets = targets  # type: ignore[attr-defined]
        return fn

    return deco


class Graph:
    def __init__(
        self,
        nodes: Mapping[str, Node],
        edges: Mapping[str, str | Router],
        start: str,
        on_error: str | None = None,
    ) -> None:
        self.nodes, self.edges, self.start, self.on_error = dict(nodes), dict(edges), start, on_error

    async def run(self, state: TurnState, ctx: TurnCtx) -> TurnState:
        name, runs = self.start, 0
        while name != END:
            if runs >= MAX_NODE_RUNS and name != self.on_error:
                state.fail(TURN_ERROR, "LOOP_GUARD")
                name = self.on_error or END
                continue
            runs += 1
            await self._step(name, state, ctx)
            if state.failure and name != self.on_error:
                name = self.on_error or END
                continue
            edge = self.edges.get(name, END)
            nxt = edge if isinstance(edge, str) else edge(state)
            if nxt != END and nxt not in self.nodes:
                state.fail(TURN_ERROR, "BAD_ROUTE")
                nxt = self.on_error or END
            name = nxt
        return state

    async def _step(self, name: str, state: TurnState, ctx: TurnCtx) -> None:
        state.last_decision, state.last_status, state.last_error, state.last_reason = None, "ok", None, None
        t0, at = time.monotonic(), datetime.now(UTC)
        status, error_code = "ok", None
        await ctx.enter(name)
        with _tracer.start_as_current_span(f"vault.node.{name}"):
            try:
                if name == self.on_error:  # persist must always run, whatever is left of the deadline
                    await self.nodes[name](state, ctx)
                else:
                    async with asyncio.timeout(ctx.budget.remaining_s()):
                        await self.nodes[name](state, ctx)
                status, error_code = state.last_status, state.last_error
            except TimeoutError:
                status = "timeout"
                state.fail(TURN_ERROR, "NODE_TIMEOUT")
            except Exception as e:  # the turn must always finish; log the type only
                if name == self.on_error:
                    raise
                code = getattr(e, "error_type", None)
                error_code = code if isinstance(code, str) else None
                _log.error("node_failed", node=name, error_type=type(e).__name__, code=error_code)
                status = "failed"
                state.fail(TURN_ERROR, error_code)
        state.steps.append(
            StepRecord(
                len(state.steps) + 1, name, status, at, int((time.monotonic() - t0) * 1000),
                decision=state.last_decision, reason_code=state.last_reason, error_code=error_code,
            )
        )  # fmt: skip

    def describe(self) -> str:
        """Mermaid flowchart: nodes as boxes, routers as diamonds with their declared targets."""
        out = ["flowchart TD", f"    start((start)) --> {self.start}"]
        for n in self.nodes:
            out.append(f'    {n}["{n}"]')
        for src, edge in self.edges.items():
            if isinstance(edge, str):
                out.append(f"    {src} --> {'end_((end))' if edge == END else edge}")
                continue
            out.append(f"    {src} --> {src}__r{{{edge.__name__}}}")
            for t in getattr(edge, "targets", ()):
                out.append(f"    {src}__r --> {'end_((end))' if t == END else t}")
        return "\n".join(out)
