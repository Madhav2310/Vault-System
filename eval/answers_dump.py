"""Answers file of a v2 run (S21.10b): `eval/reports/<run_id>.answers.jsonl`, one line per scored item.

The v2 sidecar carries metrics only. This file carries what `eval.build.audit --answers` and
`eval.judge --export-labels --answers` read (`item_id`, `answer`, `segments: [{text, passages}]`, `failed`)
plus context for humans. Passages are the stored sentences the answer cited (`eval.judge.build_case`), never
model-written quotes. Identity and text only: no keys, no model names.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping, Sequence
from typing import Any

from eval.graders import citations_of
from eval.judge import build_case
from eval.mapping import ChunkInfo
from eval.schema import GoldenItem, GoldItemV2, ItemOutput


def answers_path(run_id: object, directory: pathlib.Path) -> pathlib.Path:
    return directory / f"{run_id}.answers.jsonl"


def answer_row(
    item: GoldItemV2,
    out: ItemOutput,
    chunks: Mapping[str, ChunkInfo],
    view: GoldenItem,
    *,
    failed: bool,
) -> dict[str, Any]:
    segs = build_case(view, out, chunks).segments
    cited = list(dict.fromkeys(c.chunk_id for c in citations_of(out)))
    return {
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


ABSTAIN_MARGIN_RANK = 3  # the calibrator's `--margin-rank` default


def abstain_path(run_id: object, directory: pathlib.Path) -> pathlib.Path:
    return directory / f"{run_id}.abstain.jsonl"


class _Cand:
    """A recorded retrieval candidate in the shape `abstain.retrieval_features` reads."""

    def __init__(self, d: Mapping[str, Any]) -> None:
        self.rerank_score: float | None = d.get("rerank_score")
        self.vector_distance: float | None = d.get("vector_distance")
        self.id_match: bool = bool(d.get("id_match"))


def abstain_row(
    item: GoldItemV2, inputs: Mapping[str, Any], *, margin_rank: int = ABSTAIN_MARGIN_RANK
) -> dict[str, Any]:
    """One line of `<run_id>.abstain.jsonl`, the input of `eval.calibrate_abstain` (S22.07).

    `inputs` is what the harness read back for the run: `candidates` (final attempt: rerank_score,
    vector_distance, id_match) and `run` (the persisted `analysis.abstain`: not_found, unsupported, measured
    before removal). `top`/`margin` come from the candidates through the same function the gate uses; a
    feature the run did not record is null (never an imputed value)."""
    from app.chat.abstain import retrieval_features

    feats = retrieval_features([_Cand(c) for c in inputs.get("candidates") or ()], margin_rank)
    run = inputs.get("run") or {}
    return {
        "item_id": item.id,
        "stratum": item.stratum,
        "source_doc": item.source_doc,
        "top": feats["top"],
        "margin": feats["margin"],
        "not_found": run.get("not_found"),
        "unsupported": run.get("unsupported"),
    }


def write_abstain(rows: Sequence[Mapping[str, Any]], path: pathlib.Path) -> pathlib.Path:
    return write_answers(rows, path)


def write_answers(rows: Sequence[Mapping[str, Any]], path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), "utf-8")
    return path
