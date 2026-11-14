"""S22.09 `analyze_query@3`: same typed decisions as @2 (schema compatibility), selectable by
`chat_analyze_version` (default stays 2), shorter, and the user turn puts the question last. Fake provider."""

import json
import uuid
from typing import Any

import pytest

from app.chat.memory import Memory, Msg
from app.chat.nodes.analyze import INPUT_ORDER, AnalyzeQueryOut, ScopeDoc, analyze_query, build_inputs
from app.core.settings import Settings
from app.llm.prompts import default_registry
from app.llm.providers.fake import structured
from app.llm.routing import EFFORT_SUPPORT, ROLES
from tests.support.chat_gen import fake_gateway

D1, D2 = ScopeDoc(uuid.uuid4(), "Medalist consulting agreement"), ScopeDoc(uuid.uuid4(), "Pey Dirt agreement")
OUT: dict[str, Any] = {
    "standalone_query": "When does the Medalist consulting agreement expire?",
    "target_refs": ["D1"],
    "dimensions": ["expiry"],
    "intent": "qa",
    "needs_whole_document": False,
    "clarification": None,
}


def test_default_version_unchanged() -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    assert s.chat_analyze_version == 4


def test_v3_frontmatter_compatible_with_v2() -> None:
    reg = default_registry()
    v2, v3 = reg.get("analyze_query", 2), reg.get("analyze_query", 3)
    assert (v3.role, v3.effort, v3.output, v3.purpose) == (v2.role, v2.effort, v2.output, v2.purpose)
    assert 400 <= v3.max_tokens <= v2.max_tokens  # a meta op or a clarification still fits
    assert v3.role in ROLES and v3.effort in EFFORT_SUPPORT["gpt-6-luna"]
    assert len(v3.body) < 0.75 * len(v2.body)
    assert "untrusted" in v3.body.lower() and "<inputs>" in v3.body


def test_v3_names_every_field_the_code_reads() -> None:
    body = default_registry().get("analyze_query", 3).body
    fields = set(AnalyzeQueryOut.model_fields)
    assert fields == {
        "standalone_query", "target_refs", "dimensions", "intent", "needs_whole_document",
        "clarification", "meta_op",
    }  # fmt: skip
    for f in fields:
        assert f"{f}:" in body, f
    for op in (
        "COUNT_DOCUMENTS",
        "LIST_DOCUMENTS",
        "DOCUMENTS_MENTIONING",
        "LIST_KEY_DATES",
        "DOCUMENT_INFO",
    ):
        assert op in body
    for intent in ("qa", "compare", "summarize", "meta", "chitchat", "clarify"):
        assert f"- {intent}:" in body


def test_v3_examples_validate_against_the_schema() -> None:
    body = default_registry().get("analyze_query", 3).body
    outs = [ln[len("Output: ") :] for ln in body.splitlines() if ln.startswith("Output: ")]
    assert len(outs) >= 3
    for o in outs:
        AnalyzeQueryOut.model_validate_json(o)
    for ln in body.splitlines():
        if ln.startswith("Input: "):
            assert list(json.loads(ln[len("Input: ") :])) == list(
                INPUT_ORDER
            )  # examples match the real order


@pytest.mark.parametrize("version", [2, 3])
async def test_version_switch_same_decision(version: int) -> None:
    gw, fake = fake_gateway([structured(OUT)], chat_analyze_version=version)
    res = await analyze_query(
        gw,
        question="when does it expire?",
        memory=Memory(turns=[]),
        documents=[D1, D2],
        owner_id=uuid.uuid4(),
    )
    assert res.target_document_ids == (D1.id,) and res.dimensions == ("expiry",) and res.intent == "qa"
    assert fake.requests[0].system == default_registry().get("analyze_query", version).body


async def test_question_is_last_in_the_user_turn() -> None:
    mem = Memory(turns=[[Msg("user", "hi"), Msg("assistant", "hello")]], rolling_summary="s")
    gw, fake = fake_gateway([structured(OUT)])
    await analyze_query(gw, question="when?", memory=mem, documents=[D1], owner_id=uuid.uuid4())
    content = fake.requests[0].messages[0]["content"]
    inputs = json.loads(content[len("<inputs>") : -len("</inputs>")])
    assert list(inputs) == list(INPUT_ORDER) and INPUT_ORDER[-1] == "question"
    assert inputs == build_inputs("when?", mem, [D1], clarification_allowed=True)  # same data, new order
