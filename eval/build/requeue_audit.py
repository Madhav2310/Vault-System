"""S21.05b: requeue the stored gold-audit queue with the current judges; no new answering.

The first audit queue (`audit_queue.yaml`) was produced with the over-strict v1 claim judge (RCA
`docs/evidence/train_v2_rca.md`: 24 of 51 train failures were grader or gold artifacts). Both answers (Vault's
and the strong model's) are stored in the queue, so this re-grades them with the current claim and abstention
judges (judge calls only: no answer generation, no flagship call) and

  - drops every item where both answers now pass the gold and agree (those become auto-`verified` by
    `eval.build.apply_audit`, which verifies every audited, unqueued item);
  - keeps the rest, preserving any `decision` / `fix` / `note` already filled in;
  - writes, per kept item, a PROPOSED decision as separate fields (`proposed_decision`, `proposed_reason`,
    `proposed_fix`); `decision` is never set here. A human confirms; `apply_audit --accept-proposed` copies.

The proposal is derived by code from the judges' per-claim verdicts and the RCA's gold-audit id list:

  both answers fail claim c (the strong one saw the gold evidence only)  -> the gold is the suspect:
      gold_incomplete (gold_wrong when the RCA lists the item as CONFIDENT) with `proposed_fix` = the claims
      that do not fail for both; `ambiguous` when no claim survives
  only one answer fails, or they fail different claims                    -> system_wrong (gold kept)
  abstention strata: Vault wrong only -> system_wrong; strong wrong only -> system_wrong; both -> ambiguous

    python -m eval.build.requeue_audit --max-usd 0.5            # live (cap mandatory), rewrites the queue
    python -m eval.build.requeue_audit --dry-run                # scripted judges, writes nothing
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from eval.build.audit import (
    QUEUE_FILE,
    Audited,
    Side,
    _dry_gateway,
    _grade,
    dump_queue,
    load_queue,
    queue_entry,
    render_review,
)
from eval.judge import SpendCapReached, SpendGuard, check_tiers, gather_bounded
from eval.loader import DATASET_V2_DIR, load_dataset_v2
from eval.schema import GoldItemV2

from app.llm.gateway import LlmGateway, MemoryLedger

ROOT = DATASET_V2_DIR.parents[2]
RCA_FILE = ROOT / "docs" / "evidence" / "train_v2_rca.md"
REVIEW_FILE = DATASET_V2_DIR / "audit_review.md"
_HUMAN = ("decision", "fix", "note")

Rca = Mapping[str, tuple[str, str]]  # item id -> (confidence, issue)


def rca_gold_ids(path: pathlib.Path = RCA_FILE) -> dict[str, tuple[str, str]]:
    """Item ids of the RCA's section 7 table (gold sent to the audit): id -> (CONFIDENT|GUESS, issue)."""
    out: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return out
    in7 = False
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("## "):
            in7 = ln.startswith("## 7")
            continue
        m = re.match(r"\|\s*`([^`]+)`\s*\|(.*)\|\s*(CONFIDENT|GUESS)\s*\|\s*$", ln)
        if in7 and m:
            out[m.group(1)] = (m.group(3), " ".join(m.group(2).split()))
    return out


# --- the proposal (pure) ----------------------------------------------------------------------------------


def propose(a: Audited, rca: Rca) -> tuple[str | None, str, list[str] | None]:
    """(decision, one-line reason, fix) proposed for a queued item."""
    claims = list(a.item.required_claims)
    flag = rca.get(a.item.id)
    tag = f" RCA lists this gold ({flag[0]}): {flag[1]}." if flag else ""
    if a.errors:
        return None, "judge error on re-grading; decide by hand." + tag, None
    vf, sf = set(a.vault.failed_claims), set(a.strong.failed_claims)
    both = sorted(vf & sf)
    v_ab, s_ab = a.vault.abstain_ok is False, a.strong.abstain_ok is False
    names = ",".join(f"c{i}" for i in both)
    if both:
        keep = [c for i, c in enumerate(claims) if i not in both]
        if not keep:
            return (
                "ambiguous",
                f"neither Vault nor the strong model (gold evidence only) entails {names}; no claim survives."
                + tag,
                None,
            )
        d = "gold_wrong" if flag and flag[0] == "CONFIDENT" else "gold_incomplete"
        return (
            d,
            f"neither Vault nor the strong model (gold evidence only) entails {names}; fix drops it." + tag,
            keep,
        )
    if vf or sf:
        v = ",".join(f"c{i}" for i in sorted(vf))
        st = ",".join(f"c{i}" for i in sorted(sf))
        if vf and sf:
            why = f"Vault misses {v}, strong misses {st}; each states what the other misses; gold kept."
        elif vf:
            why = f"Vault misses {v}; the strong model entails it from the gold evidence; gold kept."
        else:
            why = f"strong model misses {st}; Vault states it; gold kept."
        return "system_wrong", why + tag, None
    if v_ab and s_ab:
        return "ambiguous", "neither answer shows the expected abstention behaviour." + tag, None
    if v_ab:
        return (
            "system_wrong",
            "Vault answers where the gold expects abstention; the strong model abstains." + tag,
            None,
        )
    if s_ab:
        return (
            "system_wrong",
            "Vault matches the expected abstention behaviour; the strong model does not." + tag,
            None,
        )
    return None, "no failing verdict after re-grading." + tag, None


# --- the requeue ------------------------------------------------------------------------------------------


@dataclass
class RequeueResult:
    document: dict[str, Any]
    dropped: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # queued ids not in the dataset (kept as they were)
    stopped: bool = False  # the spend cap stopped re-grading (the remaining entries are kept as they were)
    spent: Decimal = Decimal(0)


def _audited_from(entry: Mapping[str, Any], item: GoldItemV2) -> tuple[Audited, list[str]]:
    errors: list[str] = []
    a = Audited(
        item,
        [dict(p) for p in entry["gold_evidence"]],
        Side(entry.get("vault_answer")),
        Side(entry.get("strong_answer")),
        errors,
    )
    return a, errors


async def _regrade(gw: LlmGateway, entry: Mapping[str, Any], item: GoldItemV2, guard: SpendGuard) -> Audited:
    a, errors = _audited_from(entry, item)
    a.vault = await _grade(gw, item, a.vault.answer, guard, errors, "vault grading")
    a.strong = await _grade(gw, item, a.strong.answer, guard, errors, "strong grading")
    return a


def _count(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by = Counter(r for q in entries for r in q.get("reasons") or [])
    return {"queued": len(entries), "reasons": dict(sorted(by.items()))}


def _new_entry(old: Mapping[str, Any], a: Audited, rca: Rca) -> dict[str, Any]:
    e = queue_entry(a)
    for k in _HUMAN:
        e[k] = old.get(k)
    d, why, fix = propose(a, rca)
    e["proposed_decision"], e["proposed_reason"], e["proposed_fix"] = d, why, fix
    if a.item.id in rca:
        e["rca_gold_audit"] = rca[a.item.id][0]
    e["prev_reasons"] = list(old.get("prev_reasons") or old.get("reasons") or [])
    return e


async def requeue(
    gw: LlmGateway,
    queue: Mapping[str, Any],
    items: Mapping[str, GoldItemV2],
    guard: SpendGuard,
    rca: Rca,
    *,
    concurrency: int = 4,
) -> RequeueResult:
    check_tiers(gw.router)
    old_entries: list[dict[str, Any]] = list(queue["queue"])
    res = RequeueResult(document={})

    async def one(entry: dict[str, Any]) -> Audited | None:
        item = items.get(entry["id"])
        if item is None:
            res.unresolved.append(entry["id"])
            return None
        try:
            return await _regrade(gw, entry, item, guard)
        except SpendCapReached:
            res.stopped = True
            return None

    graded: list[Audited | None] = await gather_bounded([one(e) for e in old_entries], concurrency)
    kept: list[dict[str, Any]] = []
    for old, a in zip(old_entries, graded, strict=True):
        if a is None:
            kept.append(old)  # unresolved or cap: as it was
        elif a.queued or any(old.get(k) for k in _HUMAN):
            kept.append(_new_entry(old, a, rca))
        else:
            res.dropped.append(old["id"])
    res.spent = guard.spent
    meta = dict(queue.get("meta") or {})
    before = _count(old_entries)
    after = _count(kept)
    meta.update(
        {
            "queued": len(kept),
            "reasons": after["reasons"],
            "spent_usd": str(Decimal(str(meta.get("spent_usd", "0"))) + guard.spent),
            "requeue": {
                "judge": "claims@2 + current abstention judge, stored answers, no new answering",
                "before": before,
                "after": after,
                "dropped": len(res.dropped),
                "proposed": dict(sorted(Counter(str(q.get("proposed_decision")) for q in kept).items())),
                "unresolved": res.unresolved,
                "stopped_on_budget": res.stopped,
                "spent_usd": str(guard.spent),
            },
        }
    )
    res.document = {**queue, "meta": meta, "queue": kept}
    return res


def summary(res: RequeueResult, cap: Decimal | None) -> str:
    rq = res.document["meta"]["requeue"]
    b, a = rq["before"], rq["after"]
    reasons = sorted(set(b["reasons"]) | set(a["reasons"]))
    lines = [f"queue {b['queued']} -> {a['queued']} (dropped {len(res.dropped)})"]
    lines += [f"  {r}: {b['reasons'].get(r, 0)} -> {a['reasons'].get(r, 0)}" for r in reasons]
    lines.append("proposed: " + ", ".join(f"{k}={v}" for k, v in rq["proposed"].items()))
    if res.unresolved:
        lines.append(f"unresolved (kept as they were): {', '.join(res.unresolved)}")
    if res.stopped:
        lines.append("STOPPED by the spend cap: remaining entries kept as they were")
    lines.append(f"spent ${res.spent}" + (f" of cap ${cap}" if cap is not None else ""))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.build.requeue_audit")
    ap.add_argument("--queue", type=pathlib.Path, default=QUEUE_FILE)
    ap.add_argument(
        "--out", type=pathlib.Path, default=None, help="new queue file (default: rewrite --queue)"
    )
    ap.add_argument(
        "--review-out", type=pathlib.Path, default=None, help=f"default {REVIEW_FILE.name} beside the queue"
    )
    ap.add_argument("--rca", type=pathlib.Path, default=RCA_FILE)
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument("--dry-run", action="store_true", help="scripted FakeProvider judges, writes nothing")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args(argv)
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    ledger = MemoryLedger()
    if a.dry_run:
        gw = _dry_gateway(ledger)
    else:
        from eval.build.migrate_v1 import _live_gateway

        gw = _live_gateway(ledger)
    cap = a.max_usd
    guard = SpendGuard(ledger, cap if cap is not None else Decimal(1000))
    res = asyncio.run(
        requeue(
            gw,
            load_queue(a.queue),
            load_dataset_v2().by_id(),
            guard,
            rca_gold_ids(a.rca),
            concurrency=a.concurrency,
        )
    )
    if not a.dry_run:
        out = a.out or a.queue
        out.write_text(dump_queue(res.document), encoding="utf-8")
        review = a.review_out or (out.parent / REVIEW_FILE.name)
        review.write_text(render_review(res.document) + "\n", encoding="utf-8")
        print(f"wrote {out}\nwrote {review}")
    print(summary(res, cap))
    return 3 if res.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
