import json
import uuid
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.chat.evidence import MAX_ENUM_HANDLES, EvidenceChunk, build_evidence
from app.insights.brief import (
    BRIEF_FACTS_CAPPED,
    BriefOut,
    FactOut,
    MergeOut,
    brief_schema,
    dedupe_facts,
    fact_key,
    normalize,
    normalize_tags,
    resolve_facts,
)
from app.llm.providers.openai_chat import strict_schema
from tests.support.chat_gen import evidence


def _keys(node: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(node, dict):
        found += [v for k, v in node.items() if k == key]
        for v in node.values():
            found += _keys(v, key)
    elif isinstance(node, list):
        for v in node:
            found += _keys(v, key)
    return found


def _objects(node: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "properties" in node:
            out.append(node)
        for v in node.values():
            out += _objects(v)
    elif isinstance(node, list):
        for v in node:
            out += _objects(v)
    return out


def _payload(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "document_type": "Services Agreement",
        "summary": "A short summary.",
        "category": "contract",
        "tags": ["consulting"],
        "suggested_questions": ["Who are the parties?"],
        "facts": [],
    }
    return {**base, **kw}


def _brief(**kw: Any) -> BriefOut:
    return BriefOut.model_validate(_payload(**kw))


@pytest.mark.parametrize("model", [BriefOut, MergeOut, brief_schema(evidence())])
def test_strict_compatible(model: type[BaseModel]) -> None:
    raw = model.model_json_schema()
    assert _keys(raw, "default") == [], "no defaults anywhere"
    strict = strict_schema(raw)
    assert _keys(strict, "default") == []
    objs = _objects(strict)
    assert len(objs) >= 1
    for o in objs:
        assert o["additionalProperties"] is False
        assert o["required"] == list(o["properties"]), "every property required"
    json.dumps(strict)  # serializable


def test_category_enum() -> None:
    for ok in ("contract", "financial_report", "research_paper", "policy", "correspondence", "other"):
        assert _brief(category=ok).category == ok
    with pytest.raises(ValidationError):
        _brief(category="memo")
    with pytest.raises(ValidationError):
        MergeOut.model_validate(
            {
                "document_type": "x",
                "summary": "x",
                "category": "Contract",
                "tags": [],
                "suggested_questions": [],
            }
        )
    with pytest.raises(ValidationError):  # the field enum is closed too
        _brief(facts=[{"field": "colour", "value": "red", "evidence": []}])


def test_tags_normalized() -> None:
    raw = [
        "  Consulting ",
        "CONSULTING",
        "real  estate",
        "a b c d",
        "",
        "Risk!",
        *[f"t{i}" for i in range(12)],
    ]
    got = normalize_tags(raw)
    assert got[:3] == ["consulting", "real estate", "risk"]
    assert len(got) == 8 and len(set(got)) == 8
    assert all(t == t.lower() == t.strip() and 1 <= len(t.split()) <= 3 for t in got)
    assert normalize(_brief(tags=raw)).tags == tuple(got)


def test_facts_capped() -> None:
    facts = [{"field": "key_point", "value": f"point {i}", "evidence": []} for i in range(45)]
    n = normalize(_brief(facts=facts))
    assert len(n.facts) == 40
    assert [f.value for f in n.facts] == [f"point {i}" for i in range(40)]
    assert n.reasons == (BRIEF_FACTS_CAPPED,)
    exactly = normalize(_brief(facts=facts[:40]))
    assert len(exactly.facts) == 40 and exactly.reasons == ()


def test_handle_enum() -> None:
    ev = evidence()
    schema = brief_schema(ev)
    raw = schema.model_json_schema()
    enums = [
        d["properties"]["evidence"]["items"]["enum"] for d in _objects(raw) if "evidence" in d["properties"]
    ]
    assert enums == [list(ev.handles)]
    ok = _payload(facts=[{"field": "party", "value": "v", "evidence": [ev.handles[0]]}])
    schema.model_validate_json(json.dumps(ok))
    bad = _payload(facts=[{"field": "party", "value": "v", "evidence": ["E99.9"]}])
    with pytest.raises(ValidationError):  # the decoder cannot emit an unknown handle
        schema.model_validate(bad)


def test_handle_enum_falls_back_to_strings_above_the_limit() -> None:
    sentence = "Alpha beta gamma."
    chunks = [
        EvidenceChunk(uuid.uuid4(), uuid.uuid4(), "T", 1, 1, 1, (), sentence, ((0, len(sentence)),))
        for _ in range(MAX_ENUM_HANDLES + 1)
    ]
    ev = build_evidence(chunks)
    assert not ev.enum_ok
    assert brief_schema(ev) is BriefOut
    assert brief_schema(None) is BriefOut


def test_resolve_facts_uses_stored_text_and_drops_unknown_handles() -> None:
    ev = evidence()
    first = ev.handles[0]
    facts = [
        FactOut(field="obligation", value="model-written paraphrase", evidence=[first, "E99.0"]),
        FactOut(field="risk", value="nothing cited", evidence=[]),
    ]
    resolved, reasons = resolve_facts(facts, ev)
    assert reasons == ("CITATION_UNRESOLVED",)
    ref = resolved[0].evidence[0]
    chunk = ev.chunks[ref.chunk_id]
    assert ref.cited_text == chunk.original_text[ref.char_start : ref.char_end]
    assert ref.cited_text.startswith("The Consultant shall provide")
    assert (ref.version_no, ref.pages) == (1, [3]) and ref.section_path == ["Term"]
    assert resolved[1].evidence == ()


def test_dedupe_by_field_and_normalized_value() -> None:
    ev = evidence()
    h = ev.handles
    a = FactOut(field="amount", value="$1,250,000", evidence=[h[0]])
    b = FactOut(field="amount", value="$1250000.", evidence=[h[1]])
    c = FactOut(field="risk", value="$1250000", evidence=[h[0]])
    resolved, _ = resolve_facts([a, b, c], ev)
    out = dedupe_facts(resolved)
    assert [f.field for f in out] == ["amount", "risk"]
    assert len(out[0].evidence) == 2  # citations of the duplicate are united
    assert fact_key("party", " ACME,  Inc. ") == fact_key("party", "acme inc")
