"""Answers file of a run: `eval/reports/<run_id>.answers.jsonl`, one line per scored item.

The sidecar carries metrics only. This file carries what `eval.build.audit --answers` and
`eval.judge --export-labels --answers` read (`item_id`, `answer`, `segments: [{text, passages}]`, `failed`)
plus context for humans. Passages are the stored sentences the answer cited (`eval.judge.segment_views`),
never model-written quotes. Identity and text only: no keys, no model names.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from eval.graders import citations_of
from eval.judge import segment_views
from eval.mapping import ChunkInfo
from eval.schema import GoldItem, ItemOutput


def answers_path(run_id: object, directory: pathlib.Path) -> pathlib.Path:
    return directory / f"{run_id}.answers.jsonl"


def answer_row(
    item: GoldItem,
    out: ItemOutput,
    chunks: Mapping[str, ChunkInfo],
    *,
    failed: bool,
    judged: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    segs = segment_views(out, chunks)
    cited = list(dict.fromkeys(c.chunk_id for c in citations_of(out)))
    row: dict[str, Any] = {
        "item_id": item.id,
        "question": item.question,
        "answer": out.answer_text,
        "segments": [{"text": s.text, "passages": list(s.passages)} for s in segs],
        "cited_chunk_ids": cited,
        "abstained": out.abstained,
        "failed": failed,
        "stratum": item.stratum,
        "source_doc": item.source_doc,
    }
    if judged:  # additive: per-claim / per-segment verdicts, so a failed item is inspectable without a re-run
        row["judge"] = dict(judged)
    return row


def write_answers(rows: Sequence[Mapping[str, Any]], path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), "utf-8")
    return path
