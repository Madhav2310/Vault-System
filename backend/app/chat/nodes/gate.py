"""`gate` node: the no-LLM evidence floor (S17.08). Below the floor the turn abstains without generating."""

from collections.abc import Sequence
from typing import Any

from app.chat.abstain import AbstainConfigError, decide, load_config, retrieval_features
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
    if ctx.deps.gateway.settings.chat_abstain_scored and _scored(state, candidates, scale_ok):
        return state
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


def _scored(state: TurnState, candidates: Sequence[Any], scale_ok: bool) -> bool:
    """S22.07 scored gate (flag `chat_abstain_scored`): True when it decided the turn. Same bypasses as the
    floor; an uncalibrated config or an unscored retrieval leaves the turn to the old gate."""
    try:
        cfg = load_config()
    except AbstainConfigError:
        return False
    if not scale_ok or state.mode == "full_context" or any(c.id_match for c in candidates):
        return False
    d = decide(cfg, retrieval_features(candidates, cfg.margin_rank), stage="gate")
    if d.model is None:
        return False
    state.gate_abstain = d.outcome == "abstain"
    state.last_decision = {"passed": not state.gate_abstain, "p": round(d.p or 0.0, 4), "scored": d.model}
    return True
