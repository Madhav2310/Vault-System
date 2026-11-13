"""S18.06: the 2-5 document limit, per-document passage count and the code-written missing-side segment."""

import uuid
from decimal import Decimal
from typing import Any

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.compare import (
    COMPARE_PROMPT,
    COMPARE_SIDE_MISSING,
    COMPARE_TOO_MANY,
    compare_call,
    limit_compare,
    name_missing_sides,
    per_doc_passages,
)
from app.chat.graph import TurnState
from app.chat.nodes.analyze import AnalyzeResult, ScopeDoc
from app.chat.nodes.generate import GenerateResult
from app.llm.prompts import default_registry
from app.llm.types import Usage
from app.retrieval.scope import Scope


def _state(n: int, intent: str = "compare", dims: tuple[str, ...] = ()) -> TurnState:
    docs = [ScopeDoc(uuid.uuid4(), f"Doc {i}") for i in range(n)]
    scope = Scope(entries=tuple((d.id, uuid.uuid4()) for d in docs))
    a: Any = AnalyzeResult(intent, "q", (), dims, False, None, ())  # type: ignore[arg-type]
    return TurnState(question="q", docs=docs, scope=scope, analysis=a)


def _gen(*texts: str, kind: str = "answer") -> GenerateResult:
    segs = tuple(ResolvedSegment(i, t, "none", ()) for i, t in enumerate(texts))
    return GenerateResult(kind, Resolution(segs), (), Usage(), Decimal(0), ())


def test_per_doc_passages() -> None:
    assert [per_doc_passages(n) for n in (2, 3, 4, 5)] == [4, 3, 3, 3]


def test_limit_compare() -> None:
    for n in (2, 5):
        s = _state(n)
        limit_compare(s)
        assert s.compare and s.analysis is not None and s.analysis.intent == "compare" and not s.reasons
    s = _state(6)
    limit_compare(s)
    assert not s.compare and s.analysis is not None and s.analysis.intent == "qa"
    assert s.reasons == [COMPARE_TOO_MANY]
    s = _state(1)  # a single document: nothing to compare, no reason code
    limit_compare(s)
    assert not s.compare and s.analysis is not None and s.analysis.intent == "qa" and not s.reasons
    s = _state(3, intent="qa")
    limit_compare(s)
    assert not s.compare


def test_compare_call_lists_dimensions_and_missing() -> None:
    s = _state(2, dims=("notice", "law"))
    call = compare_call(s)  # no chunks: both documents are missing
    assert call["route"] == "compare" and call["prompt"] == COMPARE_PROMPT
    assert (
        "notice; law" in call["coverage"] and "'Doc 0'" in call["coverage"] and "'Doc 1'" in call["coverage"]
    )


def test_name_missing_sides() -> None:
    gen = _gen("In Doc 0, notice is 30 days.")
    out = name_missing_sides(gen, ["Doc 0", "Doc 1"])
    assert [s.text for s in out.resolution.segments][-1] == "Nothing relevant was found in Doc 1."
    assert len(out.resolution.segments) == 2 and COMPARE_SIDE_MISSING in out.reasons
    assert out.resolution.segments[-1].support == "none" and out.resolution.segments[-1].ordinal == 1
    named = _gen("Nothing was found in doc 1 (case-insensitive).")
    assert name_missing_sides(named, ["Doc 1"]) is named
    assert name_missing_sides(_gen(kind="abstain"), ["Doc 1"]).resolution.segments == ()


def test_prompt_contract() -> None:
    p = default_registry().get("answer_compare", 1)
    assert (p.output, p.role, p.purpose, p.version) == ("segments", "balanced", "generate", 1)
    assert "markdown table" in p.body and "Nothing was found" in p.body
