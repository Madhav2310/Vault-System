"""Calibrate the evidence floor (plan S17.08): `python -m eval.calibrate_gate --split train [--write]`.

Offline replay of retrieval, no generation and no chat-model calls (only the query embeddings, about $0.00001
each). For each item of the split it calls `Retriever.retrieve` itself and records `top_score` (the gate's own
signal, `app.chat.gate.top_score`) and the label: answerable, or the `unanswerable` category. Candidate floors
are the distinct observed scores; the chosen floor is the largest one whose false abstention on answerable
items is at most 5%. Items with an identifier match never abstain (the gate bypasses them), so they count as
answerable-or-not but are never abstained. `--write` stores the value in `backend/config/<config>.yaml`
(`gate.floor`) and turns the gate on.

Run from backend/ with PYTHONPATH=.. (needs the dev stack: `make up`):
    python -m eval.calibrate_gate --split train --config retrieval@2
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.chat.gate import top_score
from app.retrieval.config import CONFIG_DIR
from app.retrieval.scope import compute_scope

MAX_FALSE_ABSTAIN = 0.05
DEFAULT_CONFIG = "retrieval@2"


@dataclass(frozen=True)
class Row:
    item_id: str
    score: float | None  # None: retrieval produced no score (the gate lets it through)
    answerable: bool
    bypass: bool = False  # an identifier match: the gate never abstains


@dataclass(frozen=True)
class Point:
    floor: float
    false_abstain: float  # share of answerable items with score < floor
    true_abstain: float  # share of unanswerable items with score < floor


def _abstains(r: Row, floor: float) -> bool:
    return not r.bypass and r.score is not None and r.score < floor


def table(rows: Sequence[Row]) -> list[Point]:
    """One point per distinct observed score (ascending): abstain when `score < floor`."""
    answerable = [r for r in rows if r.answerable]
    unanswerable = [r for r in rows if not r.answerable]
    floors = sorted({r.score for r in rows if r.score is not None and not r.bypass})
    out: list[Point] = []
    for f in floors:
        fa = sum(_abstains(r, f) for r in answerable) / len(answerable) if answerable else 0.0
        ta = sum(_abstains(r, f) for r in unanswerable) / len(unanswerable) if unanswerable else 0.0
        out.append(Point(f, fa, ta))
    return out


def pick_floor(rows: Sequence[Row], max_false_abstain: float = MAX_FALSE_ABSTAIN) -> Point | None:
    """The largest candidate floor with false abstention <= `max_false_abstain` (None without scores)."""
    ok = [p for p in table(rows) if p.false_abstain <= max_false_abstain]
    return ok[-1] if ok else None


def write_floor(path: pathlib.Path, floor: float) -> None:
    """Set `gate.floor` and `gate.enabled: true` in a retrieval config file, keeping its comments."""
    head, sep, gate = path.read_text(encoding="utf-8").partition("\ngate:")
    gate, n1 = re.subn(r"(?m)^(  floor:\s*)\S+", lambda m: f"{m.group(1)}{floor:.6g}", gate)
    gate, n2 = re.subn(r"(?m)^(  enabled:\s*)(?:true|false)", r"\g<1>true", gate)
    if not sep or n1 != 1 or n2 != 1:
        raise SystemExit(f"{path.name}: no `gate:` block with floor and enabled lines to update")
    path.write_text(head + sep + gate, encoding="utf-8")


def render(rows: Sequence[Row], chosen: Point | None) -> str:
    n_a = sum(r.answerable for r in rows)
    lines = [
        f"items={len(rows)} answerable={n_a} unanswerable={len(rows) - n_a} "
        f"bypassed_by_identifier={sum(r.bypass for r in rows)}",
        f"{'floor':>10} {'false_abstain':>14} {'true_abstain':>13}",
    ]
    lines += [f"{p.floor:>10.4f} {p.false_abstain:>14.4f} {p.true_abstain:>13.4f}" for p in table(rows)]
    if chosen is None:
        lines.append("chosen: none (no scores)")
    else:
        lines.append(
            f"chosen floor={chosen.floor:.4f} false_abstain={chosen.false_abstain:.4f} "
            f"true_abstain={chosen.true_abstain:.4f}"
        )
    return "\n".join(lines)


async def collect(split: str, config: str) -> list[Row]:
    """Retrieve for every single-turn item of the split and record the gate signal."""
    from eval.loader import load_dataset
    from eval.pipeline_retrieval import setup

    ds = load_dataset()
    harness = await setup(config)
    sm, retriever = harness.ctx.services["sm"], harness.ctx.services["retriever"]
    owner: uuid.UUID = harness.ctx.owner_id  # type: ignore[assignment]
    rows: list[Row] = []
    try:
        for item in ds.select(split):
            if item.category == "multi_turn" or not item.question:
                continue  # a follow-up needs the analyzer's rewrite: calibrate on single turns
            ids = [uuid.UUID(harness.ctx.doc_ids[d]) for d in item.doc_ids] if item.doc_ids else None
            async with sm() as s:
                scope = await compute_scope(s, owner, ids)
                res: Any = await retriever.retrieve(s, owner, scope, item.question)
            if "no_rerank" in res.degraded:
                raise SystemExit("the reranker degraded: the floor would be calibrated on the wrong scale")
            rows.append(
                Row(
                    item.id,
                    top_score(res.candidates),
                    item.category != "unanswerable",
                    any(c.id_match for c in res.candidates),
                )
            )
    finally:
        if harness.close is not None:
            await harness.close()
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.calibrate_gate", description=__doc__.split("\n\n")[0])
    ap.add_argument("--split", default="train", choices=["train", "test", "smoke", "all"])
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="retrieval config name (default retrieval@2)")
    ap.add_argument(
        "--write", action="store_true", help="write gate.floor (and enable the gate) in the config"
    )
    args = ap.parse_args(argv)
    if args.write and args.split != "train":
        print("--write is only allowed with --split train (never tune on test content)", file=sys.stderr)
        return 2
    rows = asyncio.run(collect(args.split, args.config))
    chosen = pick_floor(rows)
    print(render(rows, chosen))
    if args.write and chosen is not None:
        write_floor(CONFIG_DIR / f"{args.config}.yaml", chosen.floor)
        print(f"wrote gate.floor={chosen.floor:.6g} to {args.config}.yaml")
    return 0 if chosen is not None and chosen.false_abstain <= MAX_FALSE_ABSTAIN else 1


if __name__ == "__main__":
    sys.exit(main())
