"""ScriptedProvider replies validate against the real strict schemas (S19.02)."""

import pathlib
from typing import Any

from app.insights.brief import BriefOut, MergeOut, brief_schema
from app.llm.fake import ScriptedProvider, load_script
from app.llm.types import ChatRequest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "ui_script.yaml"


def _req(schema: Any, user: str) -> ChatRequest:
    msgs = [{"role": "user", "content": user}]
    return ChatRequest("m", "s", msgs, 100, "low", output_schema=schema.model_json_schema())


def test_brief_and_merge_replies_validate() -> None:
    p = ScriptedProvider(load_script(SCRIPT))
    ev = "<evidence>\n[E1] T v1\nE1.0 The monthly fee is 4,500 dollars.\n</evidence>"
    brief = BriefOut.model_validate(p._brief(_req(brief_schema(None), ev), ev, facts=True))
    assert brief.facts[0].evidence == ["E1.0"] and "What is the monthly fee?" in brief.suggested_questions
    merge = MergeOut.model_validate(p._brief(_req(MergeOut, ev), ev, facts=False))
    assert brief.tone == "neutral" and brief.tone_note and brief.tone_evidence == ["E1.0"]
    assert merge.tone == "neutral" and merge.tone_note
