"""S18.07: conflict validation in code (handles resolve, >= 2 distinct chunks), schema and prompt contract."""

import uuid

from app.chat.conflicts import CONFLICT_DETECTED, MAX_CONFLICTS, conflicts_record, resolve_conflicts
from app.chat.evidence import ConflictItem, answer_schema
from app.chat.nodes.generate import generate
from app.llm.prompts import default_registry
from app.llm.providers.fake import structured
from tests.support.chat_gen import evidence, fake_gateway

ITEM = ConflictItem(topic="notice period", handles_a=["E1.0"], handles_b=["E2.0"])


def test_valid_conflict_resolves_with_stored_text() -> None:
    ev = evidence()
    [c] = resolve_conflicts([ITEM], ev)
    assert c.topic == "notice period"
    assert c.side_a[0].cited_text == "The Consultant shall provide the Services for twelve months."
    assert c.side_b[0].cited_text == "This Agreement is governed by the laws of the State of Delaware."
    rec = conflicts_record([c])
    assert rec is not None and rec["items"][0]["a"][0]["cited_text"] == c.side_a[0].cited_text


def test_single_chunk_dropped() -> None:
    ev = evidence()
    same_chunk = ConflictItem(topic="t", handles_a=["E1.0"], handles_b=["E1.1"])
    assert resolve_conflicts([same_chunk], ev) == ()
    assert conflicts_record(()) is None


def test_unresolvable_side_dropped() -> None:
    ev = evidence()
    unknown = ConflictItem(topic="t", handles_a=["E1.0"], handles_b=["E9.9"])
    empty_side = ConflictItem(topic="t", handles_a=[], handles_b=["E2.0"])
    blank_topic = ConflictItem(topic="  ", handles_a=["E1.0"], handles_b=["E2.0"])
    assert resolve_conflicts([unknown, empty_side, blank_topic], ev) == ()


def test_topic_capped_and_count_limited() -> None:
    ev = evidence()
    long_topic = ConflictItem(topic="x " * 200, handles_a=["E1.0"], handles_b=["E2.0"])
    out = resolve_conflicts([long_topic] * (MAX_CONFLICTS + 2), ev)
    assert len(out) == MAX_CONFLICTS and all(len(c.topic) <= 120 for c in out)


def test_schema_has_conflicts_with_plain_handles() -> None:
    schema = answer_schema(evidence())
    assert schema.model_validate({"status": "abstain", "segments": []}).conflicts == []  # type: ignore[attr-defined]
    defs = schema.model_json_schema()["$defs"]["ConflictItem"]
    assert defs["properties"]["handles_a"]["items"] == {"type": "string"}  # no enum: keeps the count low


async def test_generate_reports_conflict_and_reason() -> None:
    ev = evidence()
    reply = structured(
        {
            "status": "answer",
            "segments": [{"text": "The sources disagree.", "evidence": ["E1.0", "E2.0"]}],
            "conflicts": [{"topic": "term", "handles_a": ["E1.0"], "handles_b": ["E2.0"]}],
        }
    )
    gw, _ = fake_gateway([reply])
    res = await generate(gw, owner_id=uuid.uuid4(), evidence=ev, question="How long?", route="qa")
    assert len(res.conflicts) == 1 and CONFLICT_DETECTED in res.reasons
    plain = structured({"status": "answer", "segments": [{"text": "Twelve months.", "evidence": ["E1.0"]}]})
    gw, _ = fake_gateway([plain])
    res = await generate(gw, owner_id=uuid.uuid4(), evidence=ev, question="How long?", route="qa")
    assert res.conflicts == () and CONFLICT_DETECTED not in res.reasons


def test_prompt_version_and_rule() -> None:
    p = default_registry().get("answer_rag", 3)
    assert "`conflicts`" in p.body and "supersedes" in p.body
    assert default_registry().get("answer_rag", 2).version == 2  # the previous version stays loadable
