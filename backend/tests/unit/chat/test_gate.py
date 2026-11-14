"""Evidence gate: the signal, the bypasses and the template text (no model, no database)."""

from dataclasses import dataclass

from app.chat.gate import ABSTAIN_TEXT_ONE, abstain_text, evaluate, top_score
from app.explain.templates import REASON_TEMPLATES


@dataclass(frozen=True)
class C:
    rerank_score: float | None = None
    vector_distance: float | None = None
    id_match: bool = False


def test_top_score_prefers_rerank_then_vector_similarity() -> None:
    assert top_score([C(rerank_score=2.0, vector_distance=0.1), C(rerank_score=-3.0)]) == 2.0
    assert top_score([C(vector_distance=0.25), C(vector_distance=0.6)]) == 0.75  # 1 - 0.25
    assert top_score([C(), C()]) is None
    assert top_score([]) is None


def test_floor_boundary() -> None:
    cs = [C(rerank_score=1.5)]
    assert not evaluate(cs, floor=1.6, enabled=True).passed
    assert evaluate(cs, floor=1.5, enabled=True).passed  # at the floor: generation runs
    assert evaluate(cs, floor=9.0, enabled=False).reason == "disabled"


def test_bypasses() -> None:
    low = [C(rerank_score=-9.0)]
    assert not evaluate(low, floor=0.0, enabled=True).passed
    assert evaluate([*low, C(id_match=True)], floor=0.0, enabled=True).reason == "id_match"
    assert evaluate(low, floor=0.0, enabled=True, full_context=True).reason == "full_context"
    assert evaluate([C()], floor=0.0, enabled=True).reason == "no_signal"


def test_template_text() -> None:
    assert abstain_text(1) == ABSTAIN_TEXT_ONE
    assert abstain_text(1).startswith("I could not find this in your 1 document.")
    assert abstain_text(3).startswith("I could not find this in your 3 documents.")
    assert abstain_text(3) == abstain_text(3)  # deterministic: code-written, no model text
    assert "ABSTAIN_NO_EVIDENCE" in REASON_TEMPLATES
