"""Re-judge stored answers with the current judge prompts; no re-answering.

    python -m eval.rejudge --answers eval/reports/<run_id>.answers.jsonl --max-usd 0.6

Reads the answers dump (answer text and per-segment cited passages), judges again (claims, support,
abstention) and writes `<run_id>.rejudged.json`: per item the old metrics (from the run's sidecar
`<run_id>.json` next to the answers file, when present), the new metrics, the per-claim / per-segment
verdicts and the old-vs-new diff. Stdout: the diff table of every item whose verdict changed and the
aggregate means. A judge-validation result, never a baseline. `--write-sidecar` also writes
`<run_id>.rejudged.sidecar.json` (original sidecar, new metrics, new judge versions in the fingerprint).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import pathlib
import sys
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from app.llm.gateway import LlmGateway, MemoryLedger
from eval import fingerprint
from eval.judge import (
    PROMPTS,
    SegmentView,
    SpendCapReached,
    SpendGuard,
    abstain_applicable,
    check_tiers,
    gather_bounded,
    judge_abstention,
    judge_claims,
    judge_support,
    overrules,
    verdict_record,
)
from eval.schema import GoldItem

METRICS = ("answer_pass", "claim_completeness", "support_rate", "abstention_correct")


def load_rows(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]


def output_path(answers: pathlib.Path, out_dir: str | None) -> pathlib.Path:
    """`<run_id>.rejudged.json` beside the answers file, or in `out_dir` when one is named."""
    return (pathlib.Path(out_dir) if out_dir else answers.parent) / f"{run_id_of(answers)}.rejudged.json"


def run_id_of(path: pathlib.Path) -> str:
    return path.name.removesuffix(".answers.jsonl")


def load_old_metrics(sidecar: pathlib.Path) -> dict[str, dict[str, float]]:
    if not sidecar.exists():
        return {}
    return {i["item_id"]: i["metrics"] for i in json.loads(sidecar.read_text("utf-8"))["items"]}


async def rejudge_row(
    gw: LlmGateway, item: GoldItem, row: Mapping[str, Any], guard: SpendGuard
) -> dict[str, Any]:
    answer = str(row.get("answer") or "")
    has = bool(answer.strip())
    metrics: dict[str, float] = {}
    errors: list[str] = []
    claims = support = abstain = None
    if item.required_claims and has:
        try:
            claims = await judge_claims(gw, item, answer, guard=guard)
        except SpendCapReached:
            raise
        except Exception as e:  # recorded per item; one bad judge call must not sink the re-judge
            errors.append(f"judge_claims: {type(e).__name__}: {e}")
    elif item.required_claims:
        metrics["claim_completeness"] = 0.0
        metrics["answer_pass"] = 0.0
    if abstain_applicable(item):
        if has:
            try:
                abstain = await judge_abstention(gw, item, answer, guard=guard)
                metrics["abstention_correct"] = float(abstain.correct)
            except SpendCapReached:
                raise
            except Exception as e:
                errors.append(f"judge_abstain: {type(e).__name__}: {e}")
        else:
            metrics["abstention_correct"] = float(
                item.answer_type == "abstain" and bool(row.get("abstained"))
            )
    views = [SegmentView(s["text"], tuple(s["passages"])) for s in row.get("segments") or []]
    if any(v.passages for v in views):
        try:
            support = await judge_support(
                gw,
                views,
                guard=guard,
                question=item.question,
                documents=[item.source_doc] if item.source_doc else [],
            )
        except SpendCapReached:
            raise
        except Exception as e:
            errors.append(f"judge_support: {type(e).__name__}: {e}")
    if claims is not None:
        metrics["claim_completeness"] = claims.completeness
        metrics["answer_pass"] = float(claims.answer_pass)
    if support is not None and not math.isnan(support.rate):
        metrics["support_rate"] = support.rate
    if claims is not None or support is not None:
        metrics["judge_quote_overrules"] = float(overrules(claims, support))
    if item.answer_type == "abstain" and "abstention_correct" in metrics:
        metrics["answer_pass"] = metrics["abstention_correct"]
    return {"metrics": metrics, "errors": errors, "judge": verdict_record(claims, support, abstain)}


def rejudged_sidecar(
    old: Mapping[str, Any],
    res: Mapping[str, Any],
    judge: Mapping[str, str],
    code: Mapping[str, Any],
) -> dict[str, Any]:
    """A sidecar of the re-judged items: the original run's sidecar with the judged metrics replaced, the
    judge versions of this re-judge and the current eval code in the fingerprint (answer side unchanged)."""
    judged = res["items"]
    items = []
    for it in old["items"]:
        r = judged.get(it["item_id"])
        if r is None:
            continue
        kept = {k: v for k, v in it["metrics"].items() if k not in (*METRICS, "judge_quote_overrules")}
        items.append({**it, "metrics": {**kept, **r["new"]}})
    out = {**old, "run_id": f"{old['run_id']}.rejudged", "items": items}
    fp = old.get("fingerprint")
    if fp is not None:
        ids = sorted(i["item_id"] for i in items)
        out["fingerprint"] = {
            **fp,
            "judge": dict(judge),
            "code": dict(code),
            "items": {"n": len(ids), "ids": ids, "sha": fingerprint.digest(ids)},
        }
    return out


def _changed(old: float | None, new: float | None) -> bool:
    if old is None or new is None:
        return old != new
    return abs(old - new) > 1e-9


def diff_rows(
    new: Mapping[str, Mapping[str, float]], old: Mapping[str, Mapping[str, float]]
) -> list[dict[str, Any]]:
    out = []
    for iid, nm in new.items():
        om = old.get(iid, {})
        ch = {m: (om.get(m), nm.get(m)) for m in METRICS if _changed(om.get(m), nm.get(m))}
        if ch:
            out.append({"item_id": iid, "changes": {m: {"old": o, "new": n} for m, (o, n) in ch.items()}})
    return out


def means(ms: Mapping[str, Mapping[str, float]]) -> dict[str, tuple[float, int]]:
    res: dict[str, tuple[float, int]] = {}
    for m in (*METRICS, "judge_quote_overrules"):
        vs = [v[m] for v in ms.values() if m in v and not math.isnan(v[m])]
        if vs:
            res[m] = (sum(vs) / len(vs), len(vs))
    return res


def _f(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def format_diff(
    diff: Sequence[Mapping[str, Any]],
    new_means: Mapping[str, tuple[float, int]],
    old_means: Mapping[str, tuple[float, int]],
) -> str:
    lines = ["| item | metric | old | new |", "|---|---|---:|---:|"]
    for d in diff:
        for m, c in d["changes"].items():
            lines.append(f"| {d['item_id']} | {m} | {_f(c['old'])} | {_f(c['new'])} |")
    lines += ["", "| metric | n (new) | old mean | new mean |", "|---|---:|---:|---:|"]
    for m, (v, n) in new_means.items():
        o = old_means.get(m)
        lines.append(f"| {m} | {n} | {_f(o[0]) if o else '-'} | {v:.3f} |")
    return "\n".join(lines)


async def rejudge(
    gw: LlmGateway,
    rows: Sequence[Mapping[str, Any]],
    items: Mapping[str, GoldItem],
    guard: SpendGuard,
    *,
    old: Mapping[str, Mapping[str, float]] | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    old = old or {}
    todo = [r for r in rows if r["item_id"] in items]
    stopped = False

    async def one(r: Mapping[str, Any]) -> dict[str, Any] | None:
        nonlocal stopped
        try:
            return await rejudge_row(gw, items[r["item_id"]], r, guard)
        except SpendCapReached:
            stopped = True
            return None

    res: list[dict[str, Any] | None] = await gather_bounded([one(r) for r in todo], limit)
    done = {r["item_id"]: x for r, x in zip(todo, res, strict=True) if x is not None}
    new = {i: x["metrics"] for i, x in done.items()}
    old_sel = {i: old[i] for i in new if i in old}
    return {
        "items": {
            i: {"old": old.get(i), "new": x["metrics"], "errors": x["errors"], "judge": x["judge"]}
            for i, x in done.items()
        },
        "diff": diff_rows(new, old_sel) if old else [],
        "means_new": {k: {"mean": v, "n": n} for k, (v, n) in means(new).items()},
        "means_old": {k: {"mean": v, "n": n} for k, (v, n) in means(old_sel).items()},
        "stopped": "budget" if stopped else None,
        "n_judged": len(done),
        "n_skipped_unknown_item": len(rows) - len(todo),
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.rejudge")
    ap.add_argument("--answers", required=True, help="<run_id>.answers.jsonl")
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory)")
    ap.add_argument(
        "--old", default=None, help="sidecar of the original run (default: <run_id>.json beside --answers)"
    )
    ap.add_argument("--out-dir", default=None, help="where to write (default: the folder of --answers)")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing rejudged result")
    ap.add_argument(
        "--write-sidecar",
        action="store_true",
        help="also write <run_id>.rejudged.sidecar.json: the original sidecar with the new metrics and "
        "judge versions, usable by eval.paired",
    )
    ap.add_argument("--limit", type=int, default=0, help="judge only the first N rows (0 = all)")
    a = ap.parse_args(argv)
    if a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    from eval.judge import live_gateway
    from eval.loader import load_dataset

    path = pathlib.Path(a.answers)
    out = output_path(path, a.out_dir)
    sc_out = out.with_name(f"{run_id_of(path)}.rejudged.sidecar.json")
    clash = [p for p in (out, sc_out if a.write_sidecar else None) if p is not None and p.exists()]
    if clash and not a.overwrite:
        print(f"refusing to overwrite {', '.join(map(str, clash))} (pass --overwrite)", file=sys.stderr)
        return 2
    rows = load_rows(path)
    if a.limit:
        rows = rows[: a.limit]
    old = load_old_metrics(pathlib.Path(a.old) if a.old else path.with_name(f"{run_id_of(path)}.json"))
    ledger = MemoryLedger()
    gw = live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd)
    check_tiers(gw.router)
    res = asyncio.run(rejudge(gw, rows, load_dataset().by_id(), guard, old=old))
    res["run_id"] = run_id_of(path)
    res["spent_usd"] = float(guard.spent)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False, sort_keys=True), "utf-8")
    nm = {k: (v["mean"], v["n"]) for k, v in res["means_new"].items()}
    om = {k: (v["mean"], v["n"]) for k, v in res["means_old"].items()}
    print(format_diff(res["diff"], nm, om))
    print(
        f"judged {res['n_judged']} items, stopped={res['stopped']}, spent ${guard.spent} of cap ${guard.cap}"
    )
    print(f"wrote {out}")
    if a.write_sidecar:
        src = pathlib.Path(a.old) if a.old else path.with_name(f"{run_id_of(path)}.json")
        if not src.exists():
            print(f"--write-sidecar needs the original sidecar ({src})", file=sys.stderr)
            return 2
        old_sc = json.loads(src.read_text("utf-8"))
        sc = rejudged_sidecar(old_sc, res, fingerprint.judge_versions(PROMPTS), fingerprint.code_version())
        sc_out.write_text(json.dumps(sc, indent=1, ensure_ascii=False), "utf-8")
        print(f"wrote {sc_out}")
    return 0 if res["stopped"] is None else 3


if __name__ == "__main__":
    raise SystemExit(main())
