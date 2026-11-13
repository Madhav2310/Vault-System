"""Extraction-config sweep: score pdfplumber configs by how many expert gold quotes they make locatable.

Zero LLM cost. This is stage 1 of the convergence engine (docs/research/06-convergence-engine.md):
upstream text quality is tuned first, because retrieval and citations can't recover from garbled text.

Run: make eval-build   (or: cd backend && uv run --frozen --group eval python ../eval/build/extraction_sweep.py)
"""

from __future__ import annotations

import json
import pathlib
import sys

import pdfplumber
import yaml
from rapidfuzz import fuzz

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_external import FUZZ_MIN, REF, ROOT, norm

DS = ROOT / "eval" / "datasets" / "vault_eval_v1"
CONFIGS = {
    "default": {},
    "text_flow": {"use_text_flow": True},
    "text_flow+xtr0.15": {"use_text_flow": True, "x_tolerance_ratio": 0.15},
    "xtr0.15": {"x_tolerance_ratio": 0.15},
}


def gold_quotes() -> dict[str, list[str]]:
    docs = yaml.safe_load(open(DS / "documents.yaml"))["documents"]
    out: dict[str, list[str]] = {d: [] for d in docs}
    for f in ("external.yaml", "external_rejected.yaml"):
        data = yaml.safe_load(open(DS / f))
        for it in data.get("items", data.get("rejected", [])):
            doc = it.get("doc_ids", [it["id"].split(":")[0]])[0]
            out[doc] += [g["quote"] for g in it["gold"]]
    return out


def main() -> None:
    docs = yaml.safe_load(open(DS / "documents.yaml"))["documents"]
    quotes = gold_quotes()
    results: dict[str, dict[str, list[float]]] = {c: {} for c in CONFIGS}
    for doc_id, meta in docs.items():
        qs = [norm(REF.sub("", q))[:300] for q in quotes[doc_id] if q]
        if not qs:
            continue
        with pdfplumber.open(ROOT / meta["file"]) as pdf:
            pages = [pg.dedupe_chars() for pg in pdf.pages]
            for name, cfg in CONFIGS.items():
                texts = [norm(p.extract_text(**cfg) or "") for p in pages]
                results[name][doc_id] = [max(fuzz.partial_ratio(q, t) for t in texts) for q in qs]
    summary = {}
    for name, per_doc in results.items():
        allsc = [s for v in per_doc.values() for s in v]
        by_type: dict[str, list[float]] = {}
        for d, v in per_doc.items():
            by_type.setdefault(docs[d]["type"], []).extend(v)
        summary[name] = {
            "located": sum(s >= FUZZ_MIN for s in allsc),
            "total": len(allsc),
            "exactish_ge97": sum(s >= 97 for s in allsc),
            "by_type": {t: f"{sum(s >= FUZZ_MIN for s in v)}/{len(v)}" for t, v in by_type.items()},
        }
        print(
            f"{name:20s} located {summary[name]['located']}/{len(allsc)}  >=97: {summary[name]['exactish_ge97']}  {summary[name]['by_type']}"
        )
    json.dump(summary, open(DS / "extraction_sweep.json", "w"), indent=2)


if __name__ == "__main__":
    main()
