"""Evidence-stage diagnosis of a stored answers run, with no model calls.

For every answerable item it finds where each gold passage ranks in the stored candidate pool, whether the
answer step received it, and what a different evidence selection (global `top_n`, per-document top-k)
would have kept. The failure of each failed item is attributed to one stage: gold not locatable,
retrieval miss, evidence cut, abstention, or generation/judge (gold was in the evidence).

    make eval-diagnose RUN=<run_id> [REUSE=eval/.reuse_dev.json]
"""

import argparse
import asyncio
import collections
import datetime as dt
import json
import pathlib
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

REPORTS = pathlib.Path(__file__).resolve().parent / "reports"
DEFAULT_REUSE = pathlib.Path(__file__).resolve().parent / ".reuse_dev.json"
SETTINGS = ((8, 0), (16, 0), (8, 6), (12, 6))  # (global top_n, per-document top-k); (8, 0) is the baseline


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    doc: str | None
    tokens: int


@dataclass
class Replay:
    all_gold: bool
    multi_doc: bool
    tokens: int
    kept: int = 0
    selected: list[str] = field(default_factory=list)


def select(order: Sequence[Candidate], top_n: int, per_doc: int) -> list[Candidate]:
    """Global top `top_n`, then each document's own best `per_doc` chunks."""
    out = list(order[:top_n])
    if per_doc <= 0 or len({c.doc for c in order}) <= 1:
        return out
    have = collections.Counter(c.doc for c in out)
    chosen = {c.chunk_id for c in out}
    for c in order:
        if c.chunk_id in chosen or have[c.doc] >= per_doc:
            continue
        chosen.add(c.chunk_id)
        have[c.doc] += 1
        out.append(c)
    return out


def replay(
    order: Sequence[Candidate], gold_sets: Sequence[Sequence[str]], top_n: int, per_doc: int
) -> Replay:
    """Would every gold target (any chunk of its set) be in the evidence under this selection?"""
    kept = select(order, top_n, per_doc)
    ids = {c.chunk_id for c in kept}
    return Replay(
        all(bool(set(g) & ids) for g in gold_sets),
        len({c.doc for c in order}) > 1,
        sum(c.tokens for c in kept),
        len(kept),
        [c.chunk_id for c in kept],
    )


def classify(
    *, mapped: int, has_run: bool, ranks: Sequence[int | None], selected: Sequence[bool], abstained: bool
) -> str:
    """The stage that explains a failed answerable item."""
    if mapped == 0:
        return "gold not locatable in chunks"
    if not has_run:
        return "no run matched"
    if any(r is None for r in ranks):
        return "retrieval miss (a gold passage not in the candidate pool)"
    if not all(selected):
        return "evidence cut (in pool, not selected)"
    if abstained:
        return "abstained although gold was in evidence"
    return "generation or judge (gold in evidence, claim missed)"


def summarize(rows: Sequence[dict]) -> list[str]:
    have = [r for r in rows if "order" in r]
    lines = [
        f"items with located gold and a stored run: {len(have)}",
        "",
        "| selection | all gold in evidence | multi-document | evidence tokens median / p90 / max |",
        "|---|---|---|---|",
    ]
    for top_n, per_doc in SETTINGS:
        res = [replay([Candidate(*c) for c in r["order"]], r["gold_sets"], top_n, per_doc) for r in have]
        multi = [x for x in res if x.multi_doc]
        toks = sorted(x.tokens for x in res)
        p90 = toks[int(0.9 * (len(toks) - 1))] if toks else 0
        label = f"top_n {top_n}" + (f" + per-document top-{per_doc}" if per_doc else "")
        med = int(statistics.median(toks)) if toks else 0
        lines.append(
            f"| {label} | {sum(x.all_gold for x in res)}/{len(res)} | "
            f"{sum(x.all_gold for x in multi)}/{len(multi)} | {med} / {p90} / {toks[-1] if toks else 0} |"
        )
    return lines


async def _load(run: str, cache: dict, until: dt.datetime) -> list[dict]:
    from sqlalchemy import text

    from app.db.session import get_sessionmaker
    from eval.build.build_v2_docs import document_units, load_documents_v2
    from eval.loader import load_dataset_v2
    from eval.mapping import chunks_from_rows
    from eval.run_v2 import gold_targets
    from eval.splits_v2 import live_items, select_v2

    uid, doc_ids = cache["uid"], cache["doc_ids"]
    by_id = {v: k for k, v in doc_ids.items()}
    report = json.loads((REPORTS / f"{run}.json").read_text())
    items = {i.id: i for i in select_v2(live_items(load_dataset_v2().items), report["split"])}
    side = {i["item_id"]: i for i in report["items"]}
    answers = {
        json.loads(ln)["item_id"]: json.loads(ln)
        for ln in (REPORTS / f"{run}.answers.jsonl").read_text().splitlines()
    }
    docs = load_documents_v2()
    async with get_sessionmaker()() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id, document_id, ordinal, original_text, sentences, page_start, page_end, "
                        "kind, token_count "
                        "FROM chunks WHERE owner_id = :o"
                    ),
                    {"o": uid},
                )
            )
            .mappings()
            .all()
        )
        tok = {str(r["id"]): r["token_count"] for r in rows}
        doc_of = {str(r["id"]): by_id.get(str(r["document_id"])) for r in rows}
        chunks = chunks_from_rows([dict(r) for r in rows if str(r["document_id"]) in by_id], by_id)
        by_doc: dict[str, list] = collections.defaultdict(list)
        for c in chunks:
            by_doc[c.doc_id].append(c)
        runs = (
            await s.execute(
                text(
                    "SELECT r.id, u.content, r.created_at FROM rag_runs r "
                    "JOIN chat_messages a ON a.id = r.assistant_message_id "
                    "JOIN chat_messages u ON u.session_id = a.session_id AND u.role = 'user' "
                    "AND u.created_at < a.created_at "
                    "WHERE r.owner_id = :o AND r.created_at <= :t ORDER BY r.created_at"
                ),
                {"o": uid, "t": until},
            )
        ).all()
        run_by_q: dict[str, str] = {}
        for rid, q, _ in runs:  # the latest run of each question at or before `until`
            run_by_q[q] = str(rid)
        cand: dict[str, list] = {}
        for rid in set(run_by_q.values()):
            cand[rid] = (
                await s.execute(
                    text(
                        "SELECT chunk_id, attempt, selected FROM retrieval_candidates WHERE run_id = :r "
                        "ORDER BY attempt DESC, rerank_score DESC NULLS LAST, rrf_score DESC NULLS LAST"
                    ),
                    {"r": rid},
                )
            ).all()
    units: dict[str, list[str] | None] = {}

    def units_of(k: str) -> list[str] | None:
        if k not in units:
            units[k] = document_units(k, docs[k]) if k in docs else None
        return units[k]

    out = []
    for iid, it in items.items():
        sc, a = side.get(iid), answers.get(iid, {})
        if sc is None:
            continue
        rec: dict = {
            "id": iid,
            "stratum": it.stratum,
            "answer_pass": sc["metrics"].get("answer_pass"),
            "abstained": bool(a.get("abstained")),
            "answer_type": it.answer_type,
            "question": it.question,
            "answer": (a.get("answer") or "")[:700],
        }
        targets, _ = gold_targets(it, by_doc, units_of)
        mapped = [t for t in targets if t.mapped]
        rid = run_by_q.get(it.question)
        rec["mapped"], rec["has_run"] = len(mapped), rid is not None
        if rid and mapped:
            cs = cand[rid]
            last = max((c[1] for c in cs), default=0)
            order = [str(c[0]) for c in cs if c[1] == last]
            selected = {str(c[0]) for c in cs if c[1] == last and c[2]}
            rec["ranks"] = [
                min((order.index(c) + 1 for c in t.chunk_ids if c in order), default=None) for t in mapped
            ]
            rec["selected"] = [bool(set(t.chunk_ids) & selected) for t in mapped]
            rec["order"] = [(c, doc_of.get(c), tok.get(c) or 300) for c in order]
            rec["gold_sets"] = [sorted(set(t.chunk_ids)) for t in mapped]
            rec["n_selected"] = len(selected)
        judge = a.get("judge") or {}
        rec["failed_claims"] = [
            it.required_claims[c["index"]]
            for c in judge.get("claims") or []
            if not c.get("decision") and c["index"] < len(it.required_claims)
        ]
        out.append(rec)
    return out


def report_lines(rows: list[dict]) -> list[str]:
    answerable = [r for r in rows if r["answer_type"] != "abstain"]
    fails = [r for r in answerable if r["answer_pass"] == 0.0]
    stages: collections.Counter[str] = collections.Counter()
    for r in fails:
        r["stage"] = classify(
            mapped=r["mapped"],
            has_run=r["has_run"],
            ranks=r.get("ranks", []),
            selected=r.get("selected", []),
            abstained=r["abstained"],
        )
        stages[r["stage"]] += 1
    have = [r for r in answerable if "order" in r]
    lines = [
        f"answerable items {len(answerable)}, failed {len(fails)}",
        f"gold in candidate pool: {sum(all(x is not None for x in r['ranks']) for r in have)}/{len(have)}",
        f"gold in the answer step's evidence: {sum(all(r['selected']) for r in have)}/{len(have)}",
        "",
    ]
    lines += [*summarize(rows), "", "failed items by stage:"]
    lines += [f"  {n:3d}  {stage}" for stage, n in stages.most_common()]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", required=True, help="run id of an answers run in eval/reports/")
    ap.add_argument("--reuse", type=pathlib.Path, default=DEFAULT_REUSE, help="the reuse-cache of the corpus")
    ap.add_argument(
        "--until",
        help="ISO time: use each question's latest stored run at or before it "
        "(default: the answers file's modified time + 20 seconds)",
    )
    ap.add_argument("--json", type=pathlib.Path, help="write the per-item rows here")
    args = ap.parse_args(argv)
    mtime = dt.datetime.fromtimestamp((REPORTS / f"{args.run}.answers.jsonl").stat().st_mtime, dt.UTC)
    until = dt.datetime.fromisoformat(args.until) if args.until else mtime + dt.timedelta(seconds=20)
    rows = asyncio.run(_load(args.run, json.loads(args.reuse.read_text()), until))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1, default=str))
    print("\n".join(report_lines(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
