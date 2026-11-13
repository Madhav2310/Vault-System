"""Toy fixtures for eval-harness tests: scripted pipeline, hand-made chunks and golden items. No real RAG."""

import pathlib
import re
import sys
import uuid
from typing import Any

# `eval/` lives at the repo root, outside backend/: make it importable for tests.
_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from eval.mapping import ChunkInfo  # noqa: E402
from eval.schema import (  # noqa: E402
    AnswerSegment,
    CitedRange,
    EvalContext,
    GoldenItem,
    ItemOutput,
    Query,
)

REPO_ROOT = _ROOT


def chunk(cid: str, doc: str, text: str, page: int = 1, ordinal: int = 0, kind: str = "text") -> ChunkInfo:
    """A chunk whose sentences are split after '.', '?' or '!' followed by a space."""
    spans, start = [], 0
    for m in re.finditer(r"[.?!](?:\s+|$)", text):
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return ChunkInfo(cid, doc, ordinal, text, tuple(spans), page, page, kind)


def item(**kw: Any) -> GoldenItem:
    """A valid answerable lookup item over document d1; override any field."""
    base: dict[str, Any] = {
        "id": "toy:lookup",
        "source": "authored",
        "category": "lookup",
        "doc_ids": ["d1"],
        "question": "Which law governs?",
        "expected_answer": "Virginia",
        "expected_coverage": "supported",
        "grading": "judge",
        "status": "draft",
        "gold": [
            {
                "quote": "The Agreement is governed by the laws of Virginia.",
                "page": 1,
                "match": "exact",
                "score": 100,
            }
        ],
    }
    base.update(kw)
    return GoldenItem.model_validate(base)


def out(
    *segments: tuple[str, list[tuple[str, int, int]]], retrieved: list[str] | None = None, **kw: Any
) -> ItemOutput:
    segs = [AnswerSegment(t, tuple(CitedRange(*c) for c in cites)) for t, cites in segments]
    return ItemOutput(retrieved=retrieved or [], segments=segs, **kw)


class Tracker:
    """Ids an eval integration test created, for fixture teardown."""

    def __init__(self) -> None:
        self.run_ids: list[uuid.UUID] = []
        self.session_owner_ids: list[uuid.UUID] = []


class ScriptedPipeline:
    """Returns `outputs[item_id]` (a list per turn for multi-turn items); records every call."""

    def __init__(
        self, outputs: dict[str, ItemOutput | list[ItemOutput]], raises: set[str] | None = None
    ) -> None:
        self.outputs, self.raises = outputs, raises or set()
        self.calls: list[tuple[Query, EvalContext]] = []
        self.session_ids: list[Any] = []
        self.history_lens: list[int] = []

    async def __call__(self, q: Query, ctx: EvalContext) -> ItemOutput:
        self.calls.append((q, ctx))
        self.session_ids.append(ctx.session_id)
        self.history_lens.append(len(ctx.history))
        key = f"{q.item_id}#t{q.turn_index + 1}" if q.n_turns > 1 else q.item_id
        if key in self.raises:
            raise RuntimeError(f"scripted failure for {key}")
        o = self.outputs[q.item_id]
        return o[q.turn_index] if isinstance(o, list) else o
