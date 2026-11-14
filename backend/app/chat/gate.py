"""Evidence gate v1, the no-LLM floor. Pure functions; the `gate` node applies them.

`top_score` is one number per retrieval: the best rerank score when the reranker ran, else the best vector
similarity (`1 - distance`). A turn abstains without generating when that score is below `gate.floor`, no
candidate matched an identifier, and the turn is not full-context. The abstention text is a template: the
model writes nothing here. `eval/calibrate_gate.py` computes the same signal with `top_score` offline.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.explain.templates import ABSTAIN_TEXT_MANY as ABSTAIN_TEXT_MANY
from app.explain.templates import ABSTAIN_TEXT_ONE as ABSTAIN_TEXT_ONE

ABSTAIN_NO_EVIDENCE = "ABSTAIN_NO_EVIDENCE"


class Scored(Protocol):
    @property
    def rerank_score(self) -> float | None: ...
    @property
    def vector_distance(self) -> float | None: ...
    @property
    def id_match(self) -> bool: ...


@dataclass(frozen=True)
class GateDecision:
    passed: bool
    top_score: float | None
    reason: str | None = None  # why it passed without the floor: disabled, full_context, id_match, no_signal


def top_score(candidates: Iterable[Scored]) -> float | None:
    """Best rerank score, else best `1 - vector_distance`; None when retrieval produced neither."""
    cands = list(candidates)
    rerank = [c.rerank_score for c in cands if c.rerank_score is not None]
    if rerank:
        return max(rerank)
    sims = [1.0 - c.vector_distance for c in cands if c.vector_distance is not None]
    return max(sims) if sims else None


def evaluate(
    candidates: Iterable[Scored], *, floor: float, enabled: bool, full_context: bool = False
) -> GateDecision:
    """Apply the floor. Anything that is not a clear "below the floor" passes (the model abstains itself)."""
    cands = list(candidates)
    score = top_score(cands)
    if not enabled:
        return GateDecision(True, score, "disabled")
    if full_context:
        return GateDecision(True, score, "full_context")
    if any(c.id_match for c in cands):
        return GateDecision(True, score, "id_match")
    if score is None:
        return GateDecision(True, None, "no_signal")
    return GateDecision(score >= floor, score)


def abstain_text(documents: int) -> str:
    return ABSTAIN_TEXT_ONE if documents == 1 else ABSTAIN_TEXT_MANY.format(n=documents)
