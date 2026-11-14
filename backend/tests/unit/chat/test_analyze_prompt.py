"""`analyze_query`: the typed decision from the shipped prompt, and the user turn puts the question last.
Fake provider."""

import json
import uuid
from typing import Any

from app.chat.memory import Memory, Msg
from app.chat.nodes.analyze import INPUT_ORDER, PROMPT, ScopeDoc, analyze_query, build_inputs
from app.llm.prompts import default_registry
from app.llm.providers.fake import structured
from tests.support.chat_gen import fake_gateway

D1, D2 = ScopeDoc(uuid.uuid4(), "Corbel consulting agreement"), ScopeDoc(uuid.uuid4(), "Wrenfield agreement")
OUT: dict[str, Any] = {
    "standalone_query": "When does the Corbel consulting agreement expire?",
    "target_refs": ["D1"],
    "dimensions": ["expiry"],
    "intent": "qa",
    "needs_whole_document": False,
    "clarification": None,
}


async def test_typed_decision_from_the_prompt() -> None:
    gw, fake = fake_gateway([structured(OUT)])
    res = await analyze_query(
        gw,
        question="when does it expire?",
        memory=Memory(turns=[]),
        documents=[D1, D2],
        owner_id=uuid.uuid4(),
    )
    assert res.target_document_ids == (D1.id,) and res.dimensions == ("expiry",) and res.intent == "qa"
    assert fake.requests[0].system == default_registry().get(*PROMPT).body


async def test_question_is_last_in_the_user_turn() -> None:
    mem = Memory(turns=[[Msg("user", "hi"), Msg("assistant", "hello")]], rolling_summary="s")
    gw, fake = fake_gateway([structured(OUT)])
    await analyze_query(gw, question="when?", memory=mem, documents=[D1], owner_id=uuid.uuid4())
    content = fake.requests[0].messages[0]["content"]
    inputs = json.loads(content[len("<inputs>") : -len("</inputs>")])
    assert list(inputs) == list(INPUT_ORDER) and INPUT_ORDER[-1] == "question"
    assert inputs == build_inputs("when?", mem, [D1], clarification_allowed=True)  # same data, new order
