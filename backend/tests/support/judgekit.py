"""Scripted judge replies and small toy cases for judge tests (FakeProvider only, no network)."""

import json
from typing import Any

from app.llm.providers.fake import FakeReply, structured


def verdict_obj(
    judge_type: str, verdict: str = "pass", confidence: float = 0.9, n_criteria: int = 2
) -> dict[str, Any]:
    base: dict[str, Any] = {"reason": "scripted", "verdict": verdict}
    if judge_type == "faithfulness":
        base["unsupported_claims"] = [] if verdict == "pass" else ["a claim"]
    elif judge_type == "citation":
        base["unsupported_segments"] = [] if verdict == "pass" else [0]
    elif judge_type == "completeness":
        base["missing_facts"] = [] if verdict == "pass" else ["a fact"]
    elif judge_type == "rubric":
        met = {"pass": [True] * n_criteria, "fail": [False] * n_criteria}.get(
            verdict, [True] + [False] * (n_criteria - 1)
        )
        base["criteria_met"] = met
    else:
        raise ValueError(judge_type)
    base["confidence"] = confidence
    return base


def reply(judge_type: str, verdict: str = "pass", confidence: float = 0.9, n_criteria: int = 2) -> FakeReply:
    return structured(verdict_obj(judge_type, verdict, confidence, n_criteria))


def request_text(req: Any) -> str:
    """Everything a recorded ChatRequest sent to the model, as one string."""
    return req.system + "\n" + json.dumps(req.messages, ensure_ascii=False)
