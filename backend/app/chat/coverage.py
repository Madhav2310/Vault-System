"""Plan-first answers: the code check of the model's coverage plan (design §8.5, S22.04).

The user message lists the question's required elements as `R1..R4` (from the query analysis `dimensions`, at
most `MAX_ELEMENTS`). The answer's `plan` field, written before the segments, maps each element to the handles
that answer it. Code decides coverage: an element is covered only when at least one of its handles is in the
request's manifest (the model's `not_found` flag never overrides the handles). An uncovered element is stated
as not found in a code-written, uncited segment (`ELEMENT_NOT_FOUND`), never silently dropped. A plan that is
unusable (not a list, empty, no entry for a known element) degrades to a normal answer: `check_plan` returns
`None` and nothing is added. No model call is involved.
"""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.evidence import EvidenceSet
from app.explain.templates import not_found_text

ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
PLAN_PROMPT: tuple[str, int] = ("answer_rag", 4)
MAX_ELEMENTS = 4
MAX_ELEMENT_CHARS = 80


@dataclass(frozen=True)
class PlanCheck:
    covered: tuple[str, ...]
    not_found: tuple[str, ...]


def required_elements(dimensions: Sequence[str]) -> tuple[str, ...]:
    """The question's sub-questions or entities: the analysis dimensions, deduplicated, capped."""
    out: list[str] = []
    for d in dimensions:
        e = " ".join(str(d).split())[:MAX_ELEMENT_CHARS]
        if e and e not in out:
            out.append(e)
    return tuple(out[:MAX_ELEMENTS])


def element_ids(n: int) -> tuple[str, ...]:
    return tuple(f"R{i}" for i in range(1, min(n, MAX_ELEMENTS) + 1))


def elements_block(elements: Sequence[str]) -> str:
    """The prompt lines `R1: <element>` (elements are short analysis output, not document text)."""
    return "\n".join(f"{i}: {e}" for i, e in zip(element_ids(len(elements)), elements, strict=False))


def check_plan(plan: Any, elements: Sequence[str], evidence: EvidenceSet) -> PlanCheck | None:
    """Coverage per element, or `None` when the plan is unusable (the answer is then used as written)."""
    ids = element_ids(len(elements))
    if not ids or not isinstance(plan, list):
        return None
    handles: dict[str, set[str]] = {}
    for item in plan:
        if not isinstance(item, dict):
            continue
        key, hs = item.get("element"), item.get("handles")
        if not isinstance(key, str) or key not in ids or not isinstance(hs, list):
            continue
        handles.setdefault(key, set()).update(h for h in hs if isinstance(h, str))
    if not handles:
        return None
    covered: list[str] = []
    missing: list[str] = []
    for key, element in zip(ids, elements, strict=False):  # an element the plan omits counts as not found
        (covered if any(h in evidence.manifest for h in handles.get(key, ())) else missing).append(element)
    return PlanCheck(tuple(covered), tuple(missing))


def apply_plan(resolution: Resolution, check: PlanCheck) -> tuple[Resolution, tuple[str, ...]]:
    """Append one uncited template segment per element not found; returns the new resolution and reasons."""
    if not check.not_found:
        return resolution, ()
    segments = list(resolution.segments)
    for element in check.not_found:
        segments.append(ResolvedSegment(len(segments), not_found_text(element), "none", ()))
    return dataclasses.replace(resolution, segments=tuple(segments)), (ELEMENT_NOT_FOUND,)


def plan_call(state: Any, settings: Any, full: bool) -> dict[str, Any]:
    """`generate` keyword arguments of a plan-first turn; empty (nothing changes) unless the switch
    `chat_plan_first` is on, the turn is plain RAG (not compare, not whole-document) and the analysis found
    at least two required elements."""
    a = state.analysis
    if not settings.chat_plan_first or full or state.compare or a is None:
        return {}
    elements = required_elements(a.dimensions)
    if len(elements) < 2:
        return {}
    return {"prompt": PLAN_PROMPT, "plan_elements": elements}
