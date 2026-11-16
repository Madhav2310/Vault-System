"""Evidence-stage diagnosis of a stored answers run, with no model calls.

For every answerable item it finds where each gold passage ranks in the stored candidate pool, whether the
answer step received it, and what a different evidence selection (global `top_n`, per-document top-k)
would have kept. The failure of each failed item is attributed to one stage: gold not locatable,
retrieval miss, evidence cut, abstention, or generation/judge (gold was in the evidence).

    make eval-diagnose RUN=<run_id> [DIAG_ARGS="--reuse eval/.reuse_dev.json"]

It also builds one record per required gold claim of every answerable item (`--json`), with the stages
extracted, chunked, retrieved, selected, generated and supported (each true, false or unknown, with a reason).
Gold evidence is linked to an item, not to a claim, so the first four stages are item-level (every gold range
of the item must pass); `attribution` says so per record.
"""

import argparse
import asyncio
import collections
import datetime as dt
import json
import pathlib
import re
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

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


STAGES = ("extracted", "chunked", "retrieved", "selected", "generated", "supported")
GOLD_SUSPECT_WORDS = 45  # a claim this long is a whole verbatim clause, not an atomic claim
NEAR_VERBATIM = 92  # partial-ratio of the claim text inside the answer
NUMBER_RECALL = 0.5  # share of a claim's content words the answer must share when it repeats all numbers


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t.lower()).split())


def _numbers(t: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)*", t)}


def _st(ok: bool | None, reason: str = "") -> dict:
    return {"ok": ok, "reason": reason}


def _stated_in_answer(claim: str, answer: str) -> bool:
    c, a = _norm(claim), _norm(answer)
    if not c or not a:
        return False
    if c in a or fuzz.partial_ratio(c, a) >= NEAR_VERBATIM:
        return True
    nums = _numbers(claim)
    words = {w for w in c.split() if len(w) > 3 and not w.isdigit()}
    return bool(nums) and nums <= _numbers(answer) and (
        not words or len(words & set(a.split())) / len(words) >= NUMBER_RECALL
    )


def _evidence_stages(
    targets: Sequence[dict], has_run: bool, ranks: Sequence[int | None], selected: Sequence[bool]
) -> list[dict]:
    """extracted, chunked, retrieved, selected: item-level, every gold range of the item must pass."""
    if not targets:
        return [_st(None, "the item has no gold evidence")] * 4
    if any(t["extracted"] is False for t in targets):
        extracted = _st(False, "gold text not found in the document's chunk text")
    elif any(t["extracted"] is None for t in targets):
        extracted = _st(None, "gold text or the document's chunks unavailable")
    else:
        extracted = _st(True)
    unmapped = [t for t in targets if not t["mapped"]]
    chunked = _st(False, unmapped[0]["reason"] or "unmapped") if unmapped else _st(True)
    if unmapped:
        return [extracted, chunked, _st(None, "gold not chunked"), _st(None, "gold not chunked")]
    if not has_run:
        return [extracted, chunked, _st(None, "no stored run matched the question"), _st(None, "no stored run")]
    lost = sum(r is None for r in ranks)
    cut = sum(not s for s in selected)
    return [
        extracted,
        chunked,
        _st(False, f"{lost} of {len(ranks)} gold ranges not in the candidate pool") if lost else _st(True),
        _st(False, f"{cut} of {len(selected)} gold ranges not selected") if cut else _st(True),
    ]


def claim_records(
    *,
    claims: Sequence[str],
    targets: Sequence[dict],
    has_run: bool,
    ranks: Sequence[int | None],
    selected: Sequence[bool],
    answer: str,
    segments: Sequence[dict],
    judge: dict,
    gold_texts: Sequence[str],
) -> list[dict]:
    """One record per required claim. `generated` is the claims judge's decision; `supported` is the support
    verdict of the answer segment(s) whose text contains the judge's verified quote for the claim (unknown when
    that quote is missing or matches no segment); `cites_gold_chunk` says whether those segments' passages lie
    in the item's gold chunks. Judge-uncertainty flags: `possible_judge_error`, `gold_suspect`."""
    ev = _evidence_stages(targets, has_run, ranks, selected)
    jc = {c["index"]: c for c in judge.get("claims") or [] if c}
    js = {s["index"]: s for s in judge.get("segments") or [] if s}
    gold = [_norm(g) for g in gold_texts if g]
    out = []
    for i, claim in enumerate(claims):
        c = jc.get(i)
        if c is None:
            gen = _st(None, "no claims-judge decision stored")
        else:
            gen = _st(bool(c.get("decision")), "entailed" if c.get("decision") else "judged not entailed")
        quote = _norm((c or {}).get("quote") or "")
        seg_ix = (
            [k for k, sg in enumerate(segments) if quote in _norm(sg.get("text") or "")]
            if gen["ok"] and quote
            else []
        )
        sup, cites = _st(None, "claim not entailed, so no answer segment states it"), None
        if gen["ok"]:
            if not seg_ix:
                sup = _st(None, "no answer segment matches the judge's quote for the claim")
            elif any(k not in js for k in seg_ix):
                sup = _st(None, "no support verdict stored for the segment")
            else:
                ok = all(bool(js[k].get("decision")) for k in seg_ix)
                sup = _st(ok, f"segments {seg_ix}: " + ("judged supported" if ok else "judged unsupported"))
                if gold:
                    passages = [_norm(p) for k in seg_ix for p in segments[k].get("passages") or []]
                    cites = any(p and (p in g or g in p) for p in passages for g in gold)
        rec = {
            "index": i,
            "claim": claim,
            "attribution": "single-range" if len(targets) == 1 else f"item-level ({len(targets)} gold ranges)",
            **dict(zip(STAGES[:4], ev, strict=True)),
            "generated": gen,
            "supported": sup,
            "segments": seg_ix,
            "cites_gold_chunk": cites,
            "possible_judge_error": bool(
                gen["ok"] is False and ((c or {}).get("overruled") or _stated_in_answer(claim, answer))
            ),
            "gold_suspect": len(claim.split()) >= GOLD_SUSPECT_WORDS,
        }
        rec["first_failing_stage"] = next((s for s in STAGES if rec[s]["ok"] is False), None)
        out.append(rec)
    return out


def _utilisation(recs: Sequence[dict]) -> str:
    sel = [r for r in recs if r["selected"]["ok"]]
    known = [r for r in sel if r["generated"]["ok"] is not None]
    gen = sum(bool(r["generated"]["ok"]) for r in known)
    pct = f" ({gen / len(known):.0%})" if known else ""
    unk = f", {len(sel) - len(known)} unknown" if len(sel) > len(known) else ""
    return f"{gen}/{len(known)}{pct}{unk}"


def claim_summary(recs: Sequence[dict]) -> list[str]:
    """Counts per first-failing stage, and the evidence utilisation rate (claims whose evidence was selected
    that the judge found entailed), overall and for judge-confident claims only."""
    conf = [r for r in recs if not r["possible_judge_error"] and not r["gold_suspect"]]
    none = "none (no stage failed)"
    cnt = collections.Counter(r["first_failing_stage"] or none for r in recs)
    cnt_c = collections.Counter(r["first_failing_stage"] or none for r in conf)
    lines = [
        f"required claims of answerable items: {len(recs)}; judge-confident: {len(conf)}; "
        f"possible_judge_error: {sum(r['possible_judge_error'] for r in recs)}; "
        f"gold_suspect: {sum(r['gold_suspect'] for r in recs)}",
        "",
        "| first failing stage | all claims | judge-confident |",
        "|---|---|---|",
    ]
    lines += [f"| {st} | {cnt.get(st, 0)} | {cnt_c.get(st, 0)} |" for st in (*STAGES, none)]
    lines += [
        "",
        f"evidence utilisation, all claims: {_utilisation(recs)}",
        f"evidence utilisation, judge-confident claims: {_utilisation(conf)}",
    ]
    return lines


def _target_facts(t, doc_text: dict[str, str]) -> dict:  # t: eval.mapping.GoldTarget
    """`extracted`: the gold text is in the document's chunk text (mapped, or present across chunk boundaries)."""
    if t.mapped:
        ext: bool | None = True
    elif t.reason == "document sentences unavailable" or not doc_text.get(t.doc_id):
        ext = None
    else:
        q, d = _norm(t.quote), _norm(doc_text[t.doc_id])
        ext = bool(q) and (q in d or fuzz.partial_ratio(q, d) >= 90)
    return {"extracted": ext, "mapped": t.mapped, "reason": t.reason, "quote": t.quote}


async def _load(run: str, cache: dict, until: dt.datetime) -> list[dict]:
    from sqlalchemy import text

    from app.db.session import get_sessionmaker
    from eval.build.build_docs import document_units, load_documents
    from eval.loader import load_dataset
    from eval.mapping import chunks_from_rows
    from eval.run import gold_targets
    from eval.splits import live_items, select_split

    uid, doc_ids = cache["uid"], cache["doc_ids"]
    by_id = {v: k for k, v in doc_ids.items()}
    report = json.loads((REPORTS / f"{run}.json").read_text())
    items = {i.id: i for i in select_split(live_items(load_dataset().items), report["split"])}
    side = {i["item_id"]: i for i in report["items"]}
    answers = {
        json.loads(ln)["item_id"]: json.loads(ln)
        for ln in (REPORTS / f"{run}.answers.jsonl").read_text().splitlines()
    }
    docs = load_documents()
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
        text_of = {str(r["id"]): r["original_text"] or "" for r in rows}
        doc_text: dict[str, str] = collections.defaultdict(str)
        for r in sorted(rows, key=lambda r: (str(r["document_id"]), r["ordinal"])):
            if str(r["document_id"]) in by_id:
                doc_text[by_id[str(r["document_id"])]] += " " + (r["original_text"] or "")
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
        if it.answer_type != "abstain":
            rec["claims"] = [
                {"item_id": iid, "stratum": it.stratum, **c}
                for c in claim_records(
                    claims=it.required_claims,
                    targets=[_target_facts(t, doc_text) for t in targets],
                    has_run=rec["has_run"],
                    ranks=rec.get("ranks", []),
                    selected=rec.get("selected", []),
                    answer=a.get("answer") or "",
                    segments=a.get("segments") or [],
                    judge=judge,
                    gold_texts=[text_of.get(c, "") for t in mapped for c in sorted(t.chunk_ids)],
                )
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
    lines += ["", *claim_summary([c for r in rows for c in r.get("claims", [])])]
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
    ap.add_argument("--json", type=pathlib.Path, help="write the per-item rows (each with its per-claim records)")
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
