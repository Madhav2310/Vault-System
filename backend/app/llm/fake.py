"""Scripted model for the UI test harness (S19.02). TEST-ONLY: selected by `LLM_PROVIDER=fake`, refused when
`ENVIRONMENT=prod` (`Settings` validator). Replies are keyed by question text from a YAML script; nothing
here is a quality signal. Embeddings are the deterministic hash vectors of `FakeProvider`.

Script shape (see tests/fixtures/ui_script.yaml): `questions: [{question, intent?, suggest?, status?,
segments: [{text, cite: [substring of an evidence sentence]}]}]`. Citations are chosen by matching the
substring against the evidence lines of the request, so they do not depend on retrieval order."""

import json
import os
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.llm.providers.fake import FakeProvider, FakeReply, text
from app.llm.types import ChatRequest, ChatResponse, StreamEvent

SCRIPT_ENV = "UI_SCRIPT_PATH"
DEFAULT_SCRIPT = Path("/srv/ui_script.yaml")
_EVIDENCE_LINE = re.compile(r"^(E\d+\.\d+) (.*)$", re.M)


@dataclass(frozen=True)
class Entry:
    question: str
    intent: str
    suggest: bool
    status: str
    segments: tuple[dict[str, Any], ...]


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def load_script(path: Path | None = None) -> dict[str, Entry]:
    p = path or Path(os.environ.get(SCRIPT_ENV, str(DEFAULT_SCRIPT)))
    raw = yaml.safe_load(p.read_text()) or {}
    entries: dict[str, Entry] = {}
    for q in raw.get("questions", []):
        segs = tuple(q.get("segments", []))
        status = q.get("status", "answer" if segs else "abstain")
        entries[_norm(q["question"])] = Entry(
            q["question"], q.get("intent", "qa"), bool(q.get("suggest")), status, segs
        )
    return entries


def _user_text(req: ChatRequest) -> str:
    return "\n".join(str(m.get("content", "")) for m in req.messages if m.get("role") == "user")


def _props(req: ChatRequest) -> dict[str, Any]:
    props: dict[str, Any] = (req.output_schema or {}).get("properties", {})
    return props


def _enum(req: ChatRequest, prop: str) -> list[str]:
    node = _props(req).get(prop, {})
    ref = node.get("$ref")
    if ref:
        node = (req.output_schema or {}).get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
    return list(node.get("enum", []))


class ScriptedProvider(FakeProvider):
    """A `FakeProvider` whose chat replies are computed from the request instead of popped from a queue."""

    def __init__(self, script: dict[str, Entry] | None = None) -> None:
        super().__init__()
        self.entries = script if script is not None else load_script()

    async def complete(self, req: ChatRequest) -> ChatResponse:
        self._queue.append(self._reply(req))
        return await super().complete(req)

    async def stream(self, req: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        self._queue.append(self._reply(req))
        async for ev in super().stream(req):
            yield ev

    def _reply(self, req: ChatRequest) -> FakeReply:
        props = _props(req)
        user = _user_text(req)
        if "verdicts" in props:  # support check: every segment is supported
            m = re.search(r"<inputs>(.*)</inputs>", user, re.S)
            segs = json.loads(m.group(1)).get("segments", []) if m else []
            return text(
                json.dumps(
                    {
                        "verdicts": [
                            {"index": g["index"], "verdict": "supported", "reason": "scripted"} for g in segs
                        ]
                    }
                )
            )
        if "standalone_query" in props:
            return text(json.dumps(self._analyze(user)))
        if "segments" in props:
            return text(json.dumps(self._answer(user)))
        if "facts" in props:
            return text(json.dumps(self._brief(req, user, facts=True)))
        if "suggested_questions" in props:
            return text(json.dumps(self._brief(req, user, facts=False)))
        return text("ok")

    def _analyze(self, user: str) -> dict[str, Any]:
        m = re.search(r"<inputs>(.*)</inputs>", user, re.S)
        question = str(json.loads(m.group(1)).get("question", "")) if m else ""
        entry = self.entries.get(_norm(question))
        return {
            "standalone_query": question,
            "target_refs": [],
            "dimensions": [],
            "intent": entry.intent if entry else "qa",
            "needs_whole_document": False,
            "clarification": None,
        }

    def _answer(self, user: str) -> dict[str, Any]:
        lines = _EVIDENCE_LINE.findall(user)
        m = re.search(r"<question>\n(.*?)\n</question>", user, re.S)
        if m is None:  # a custom summary request: one cited segment from the first evidence sentence
            first = lines[:1]
            return {
                "status": "answer" if first else "abstain",
                "segments": [
                    {"text": "A scripted summary of the document.", "evidence": [h for h, _ in first]}
                ],
            }
        entry = self.entries.get(_norm(m.group(1)))
        if entry is None or entry.status == "abstain":
            return {"status": "abstain", "segments": [], "followups": []}
        segments = []
        for seg in entry.segments:
            needles = [n.lower() for n in seg.get("cite", [])]
            handles = [h for n in needles for h, line in lines if n in line.lower()][:2]
            segments.append({"text": seg["text"], "evidence": handles})
        return {"status": "answer", "segments": segments, "followups": ["What is the monthly fee?"]}

    def _brief(self, req: ChatRequest, user: str, *, facts: bool) -> dict[str, Any]:
        categories = _enum(req, "category")
        out: dict[str, Any] = {
            "document_type": "document",
            "summary": "A scripted brief of the document.",
            "category": "other" if "other" in categories else (categories or ["other"])[0],
            "tags": ["fixture"],
            "suggested_questions": [e.question for e in self.entries.values() if e.suggest],
        }
        if facts:
            lines = _EVIDENCE_LINE.findall(user)
            fact_def = (req.output_schema or {}).get("$defs", {}).get("FactOut", {})
            fields = list(fact_def.get("properties", {}).get("field", {}).get("enum", [])) or ["other"]
            out["facts"] = (
                [{"field": fields[-1], "value": lines[0][1][:60], "evidence": [lines[0][0]]}] if lines else []
            )
        return out
