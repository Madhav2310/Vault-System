"""`gate` node: the no-LLM evidence floor (S17.08). Below the floor the turn abstains without generating."""

from app.chat.gate import evaluate
from app.chat.graph import TurnCtx, TurnState
from app.retrieval.rerank import DEGRADED_NO_RERANK


async def gate(state: TurnState, ctx: TurnCtx) -> TurnState:
    cfg = ctx.deps.retriever.config.gate
    r = state.retrieval
    candidates = r.candidates if r is not None else ()
    # Without the reranker the signal is a cosine similarity, which the floor (a rerank-scale value) was not
    # calibrated on: fail open and let the model decide.
    scale_ok = r is not None and DEGRADED_NO_RERANK not in r.degraded
    d = evaluate(
        candidates,
        floor=cfg.floor,
        enabled=cfg.enabled and scale_ok,
        full_context=state.mode == "full_context",
    )
    state.gate_abstain = not d.passed
    state.last_decision = {
        "passed": d.passed,
        "top_score": None if d.top_score is None else round(d.top_score, 4),
        "floor": cfg.floor,
        "bypass": d.reason,
    }
    return state
