"""Stage attribution: assign every failed item exactly one earliest faulty stage, by code.

`python -m eval.attribution <run>.json [--vs other.json]` prints the stage table (and a run diff).
Unknown (None) signals are skipped, never guessed; a failure with no positive signal is `unknown_stage`.
Extra per-item signals the runner may add to a sidecar item live under the key `attribution`
(see `from_sidecar_item`).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

from eval.report import Sidecar, SidecarItem, cluster_ci, cluster_key

STAGES = (
    "extraction",
    "chunking",
    "retrieval",
    "ranking",
    "utilization",
    "citation",
    "generation",
    "abstention",
    "grading",
)
UNKNOWN = "unknown_stage"
Row = tuple[int, float, float, float]  # count, share of failures, ci low, ci high


@dataclass(frozen=True)
class AttributionInput:
    """What attribution needs per item. None = not recorded (that check is skipped)."""

    item_id: str
    failed: bool
    source_doc: str | None = None
    should_abstain: bool | None = None  # gold says unanswerable
    abstained: bool | None = None  # the system abstained
    gold_in_parsed: bool | None = None  # gold sentences present in the parsed text
    gold_in_chunk: bool | None = None  # some chunk holds each required claim
    gold_in_candidates: bool | None = None  # gold chunk in the pre-cut candidate list
    gold_in_evidence: bool | None = None  # gold chunk survived the cut into the evidence
    gold_cited: bool | None = None  # an answer citation lands on gold
    cited_outside_gold: bool | None = None
    unresolved_citations: int | None = None
    claim_missing_or_unsupported: bool | None = None  # judge: required claim missing or unsupported
    judge_verdict: str | None = None
    human_label: str | None = None


def attribute(a: AttributionInput) -> str | None:
    """None for a passing item, else the earliest stage whose check fires."""
    if not a.failed:
        return None
    if a.should_abstain is not True:  # evidence-side stages apply to answerable items only
        if a.gold_in_parsed is False:
            return "extraction"
        if a.gold_in_chunk is False:
            return "chunking"
        if a.gold_in_candidates is False:
            return "retrieval"
        if a.gold_in_evidence is False:
            return "ranking"
        if a.gold_in_evidence is True and a.gold_cited is False:
            return "utilization"
        if a.cited_outside_gold is True or (a.unresolved_citations or 0) > 0:
            return "citation"
        if a.claim_missing_or_unsupported is True:
            return "generation"
    if a.should_abstain is not None and a.abstained is not None and a.should_abstain != a.abstained:
        return "abstention"
    if a.human_label is not None and a.judge_verdict is not None and a.human_label != a.judge_verdict:
        return "grading"
    return UNKNOWN


def _failed(m: Mapping[str, float], error: str | None) -> bool:
    return (
        bool(error)
        or m.get("answer_pass") == 0.0
        or m.get("faithfulness_pass") == 0.0
        or m.get("citation_integrity", 1.0) < 1.0
    )


def from_sidecar_item(item: SidecarItem, extra: Mapping[str, Any] | None = None) -> AttributionInput:
    """Fill from the current sidecar (metrics, error, source_doc); `extra` = runner-recorded signals."""
    known = {f.name for f in fields(AttributionInput)} - {"item_id", "failed", "source_doc"}
    kw: dict[str, Any] = {k: v for k, v in (extra or {}).items() if k in known}
    recalls = {int(k.split("@")[1]): v for k, v in item.metrics.items() if k.startswith("recall@")}
    if "gold_in_candidates" not in kw and recalls:  # coarse: any gold hit within the deepest k recorded
        kw["gold_in_candidates"] = recalls[max(recalls)] > 0
    failed = bool((extra or {}).get("failed", _failed(item.metrics, item.error)))
    return AttributionInput(item.item_id, failed, item.source_doc, **kw)


def load_inputs(path: str) -> list[AttributionInput]:
    raw = json.loads(pathlib.Path(path).read_text())
    sc = Sidecar.model_validate(raw)
    return [from_sidecar_item(it, r.get("attribution")) for it, r in zip(sc.items, raw["items"], strict=True)]


def stage_table(inputs: Sequence[AttributionInput]) -> dict[str, Row]:
    """stage -> (count, share of failures, ci_low, ci_high); cluster bootstrap by source_doc."""
    failures = [(a, attribute(a)) for a in inputs if a.failed]
    clusters = [cluster_key(a.item_id, a.source_doc) for a, _ in failures]
    counts = Counter(s for _, s in failures)
    out: dict[str, Row] = {}
    for stage in (*STAGES, UNKNOWN):
        mean, lo, hi = cluster_ci([1.0 if s == stage else 0.0 for _, s in failures], clusters)
        out[stage] = (counts.get(stage, 0), mean, lo, hi)
    return out


def render(table: Mapping[str, Row], n_items: int) -> str:
    n_fail = sum(r[0] for r in table.values())
    lines = [
        f"items: {n_items}  failed: {n_fail}",
        "",
        "| stage | failures | share | 95% CI (clustered) |",
        "|---|---:|---:|---|",
    ]
    for stage, (c, m, lo, hi) in table.items():
        lines.append(f"| {stage} | {c} | {m:.3f} | [{lo:.3f}, {hi:.3f}] |")
    return "\n".join(lines)


def render_diff(a: Mapping[str, Row], b: Mapping[str, Row]) -> str:
    lines = ["| stage | A | B | B-A count | B-A share |", "|---|---:|---:|---:|---:|"]
    for s in a:
        lines.append(f"| {s} | {a[s][0]} | {b[s][0]} | {b[s][0] - a[s][0]:+d} | {b[s][1] - a[s][1]:+.3f} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.attribution")
    ap.add_argument("run")
    ap.add_argument("--vs")
    args = ap.parse_args(argv)
    a = load_inputs(args.run)
    ta = stage_table(a)
    print(render(ta, len(a)))
    if args.vs:
        print("\nDiff (A = run, B = --vs)\n")
        print(render_diff(ta, stage_table(load_inputs(args.vs))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
