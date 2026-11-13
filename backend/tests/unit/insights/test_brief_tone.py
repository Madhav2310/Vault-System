"""Brief tone (brief@2): schema, normalization, code-side resolution and verification, prompt pins."""

import uuid

import pytest
from pydantic import ValidationError

from app.insights.brief import (
    MAX_TONE_EVIDENCE,
    MAX_TONE_NOTE_CHARS,
    BriefOut,
    MergeOut,
    brief_schema,
    normalize,
    resolve_tone,
)
from app.insights.verify import NO_CITATION, verify_tone
from app.llm.prompts import default_registry
from tests.support.brief_kit import contract_evidence


def _out(**kw: object) -> BriefOut:
    base: dict[str, object] = {
        "document_type": "t",
        "summary": "s",
        "category": "other",
        "tags": [],
        "suggested_questions": [],
        "facts": [],
        "tone": "neutral",
        "tone_note": "plain",
        "tone_evidence": [],
    }
    return BriefOut.model_validate({**base, **kw})


def test_tone_is_a_fixed_enum_and_required() -> None:
    with pytest.raises(ValidationError):
        _out(tone="sarcastic")
    with pytest.raises(ValidationError):
        BriefOut.model_validate({k: v for k, v in _out().model_dump().items() if k != "tone"})
    with pytest.raises(ValidationError):
        MergeOut.model_validate(
            {
                "document_type": "t",
                "summary": "s",
                "category": "other",
                "tags": [],
                "suggested_questions": [],
                "tone": "neutral",
            }
        )  # tone_note missing


def test_tone_evidence_is_a_handle_enum_in_the_schema() -> None:
    ev = contract_evidence()
    schema = brief_schema(ev).model_json_schema()
    items = schema["properties"]["tone_evidence"]["items"]
    assert set(items["enum"]) == set(ev.handles)


def test_normalize_caps_note_and_dedupes_handles() -> None:
    n = normalize(_out(tone_note="x " * 300, tone_evidence=["E1.0", "E1.0", "E1.1"]))
    assert len(n.tone_note) <= MAX_TONE_NOTE_CHARS and n.tone_evidence == ("E1.0", "E1.1")


def test_resolve_tone_copies_cited_text_and_drops_unknown_handles() -> None:
    ev = contract_evidence()
    first = ev.handles[0]
    refs, reasons = resolve_tone("note", [first, "E99.9"], ev)
    assert len(refs) == 1 and reasons == ("CITATION_UNRESOLVED",)
    assert refs[0].cited_text.startswith("This Services Agreement")
    many, _ = resolve_tone("note", list(ev.handles), ev)
    assert len(many) <= MAX_TONE_EVIDENCE


def test_verify_tone_needs_a_citation_into_the_version() -> None:
    ev = contract_evidence()
    refs, _ = resolve_tone("n", [ev.handles[0]], ev)
    assert verify_tone(refs, refs[0].version_id).status == "verified"
    other = verify_tone(refs, uuid.uuid4())
    assert (other.status, other.reason) == ("unverified", NO_CITATION)
    assert verify_tone([], uuid.uuid4()).reason == NO_CITATION


def test_brief_prompts_v2_carry_tone_and_v1_still_loads() -> None:
    reg = default_registry()
    e2, m2 = reg.get("brief_extract", 2), reg.get("brief_merge", 2)
    assert "`tone_evidence`" in e2.body and "## 7. Tone" in e2.body and "## 8. Output reminder" in e2.body
    assert "`tone_note`" in m2.body and "tone evidence" in m2.body
    assert (e2.role, e2.effort, e2.output) == ("balanced", "low", "schema")
    assert "tone" not in reg.get("brief_extract", 1).body.lower().split("## 7.")[-1][:200]
    assert reg.get("brief_merge", 1).version == 1
