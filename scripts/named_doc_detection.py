"""Measure the named-document detector (`app.chat.named_docs.detect_named`) on the v2 evaluation questions.

For every live item of a split: the documents the gold expects (`gold_evidence[*].doc`) against the documents the
detector names, in the scope the item is asked in (`doc_ids`, or the whole vault when null) and, as a harder
test, always in the whole vault. Titles and opening text come from the database (read-only) through the document
ids of the reuse cache (`eval/.reuse_<split>.json`), so the detector sees what a live turn sees.

    cd backend && uv run python ../scripts/named_doc_detection.py --split dev --split train [--max-df 1]

Needs the compose postgres (`vault-postgres-1`); writes nothing.
"""

import argparse
import json
import pathlib
import subprocess
import sys
import uuid
from collections import defaultdict
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from eval.loader import load_dataset_v2  # noqa: E402
from eval.splits_v2 import select_v2  # noqa: E402

from app.chat.named_docs import LEAD_CHARS, MIN_NAMED, detect_named  # noqa: E402
from app.chat.nodes.analyze import ScopeDoc  # noqa: E402


def _psql(sql: str) -> Any:
    out = subprocess.run(
        ["docker", "exec", "-e", "PGOPTIONS=-c default_transaction_read_only=on", "vault-postgres-1",
         "psql", "-U", "vault", "-d", "vault", "-At", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    return json.loads(out or "[]")


def load_docs(owner: str) -> dict[uuid.UUID, ScopeDoc]:
    rows = _psql(
        "select json_agg(row_to_json(t)) from (select d.id, d.title, "
        f"left(c.original_text, {LEAD_CHARS}) as lead from documents d "
        "left join chunks c on c.version_id = d.current_version_id and c.owner_id = d.owner_id and c.ordinal = 0 "
        f"where d.owner_id = '{owner}' and d.deleted_at is null) t"
    )
    return {uuid.UUID(r["id"]): ScopeDoc(uuid.UUID(r["id"]), r["title"], r["lead"] or "") for r in rows}


def prf(named: set[str], gold: set[str]) -> tuple[float, float]:
    hit = len(named & gold)
    return (hit / len(named) if named else 1.0, hit / len(gold) if gold else 1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="append", default=[])
    ap.add_argument("--reuse-dir", type=pathlib.Path, default=ROOT / "eval", help="holds .reuse_SPLIT.json")
    ap.add_argument("--max-df", type=int, default=1)
    ap.add_argument("--lines", action="store_true", help="print one line per multi-document item")
    args = ap.parse_args()
    items_all = load_dataset_v2().items
    for split in args.split or ["dev", "train"]:
        reuse = json.loads((args.reuse_dir / f".reuse_{split}.json").read_text())
        key_of = {uuid.UUID(v): k for k, v in reuse["doc_ids"].items()}
        id_of = {k: uuid.UUID(v) for k, v in reuse["doc_ids"].items()}
        docs = load_docs(reuse["uid"])
        items = [i for i in select_v2(items_all, split) if i.stratum != "multi_turn"]
        agg: dict[str, list[float]] = defaultdict(list)
        lines: list[str] = []
        skipped = 0
        for it in items:
            gold = {e.doc for e in it.gold_evidence}
            if not gold or any(g not in id_of for g in gold):
                skipped += 1
                continue
            for label, keys in (("item scope", it.doc_ids), ("vault scope", None)):
                scope_ids = [id_of[k] for k in keys if k in id_of] if keys else list(docs)
                scope = [docs[i] for i in scope_ids if i in docs]
                named = {key_of.get(d, str(d)) for d in detect_named(it.question, scope, max_df=args.max_df)}
                p, r = prf(named, gold)
                fired = len(named) >= MIN_NAMED
                multi = len(gold) >= MIN_NAMED
                tag = f"{label} | {'multi' if multi else 'single'}"
                agg[f"{tag} | items"].append(1)
                agg[f"{tag} | fired"].append(fired)
                agg[f"{tag} | fired and every named doc is gold"].append(fired and named <= gold)
                agg[f"{tag} | micro hit"].append(len(named & gold))
                agg[f"{tag} | micro named"].append(len(named))
                agg[f"{tag} | micro gold"].append(len(gold))
                agg[f"{tag} | recall (mean)"].append(r)
                agg[f"{tag} | precision (mean)"].append(p)
                if multi and label == "item scope":
                    lines.append(
                        f"- `{it.id}` [{it.stratum}] P={p:.2f} R={r:.2f} fired={fired}\n"
                        f"  - Q: {it.question}\n  - gold: {sorted(gold)}\n  - named: {sorted(named)}"
                    )
        print(
            f"\n## split={split} max_df={args.max_df} items={len(items)} skipped(no gold or doc not in cache)={skipped}"
        )
        tags = sorted({k.rsplit(" | ", 1)[0] for k in agg})
        for tag in tags:
            n = len(agg[f"{tag} | items"])
            hit, named_n, gold_n = (sum(agg[f"{tag} | micro {x}"]) for x in ("hit", "named", "gold"))
            fired = sum(agg[f"{tag} | fired"])
            clean = sum(agg[f"{tag} | fired and every named doc is gold"])
            print(
                f"- {tag}: n={n} fired(>=2 named)={fired} fired-clean={clean} "
                f"micro-precision={hit / named_n if named_n else float('nan'):.3f} "
                f"micro-recall={hit / gold_n if gold_n else float('nan'):.3f}"
            )
        if args.lines:
            print("\n".join(lines))


if __name__ == "__main__":
    main()
