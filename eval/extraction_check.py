"""Gold-quote locatability with the production PDF extractor (S1.13 AC8; zero LLM cost).

Same scoring as the extraction sweep (eval/build/extraction_sweep.py): a quote counts as located when its
normalized text has partial_ratio >= FUZZ_MIN against some normalized page text. The difference is that page
text now comes from `app.ingestion.extract.pdf.PdfExtractor` (the glyph-mapped stream), not `extract_text()`.

Run: make eval-extraction   (prints located_pct; exits 1 below --min, default 99)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import yaml
from rapidfuzz import fuzz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval" / "build"))
sys.path.insert(0, str(ROOT / "backend"))
from build_external import FUZZ_MIN, REF, norm  # noqa: E402
from extraction_sweep import DS, gold_quotes  # noqa: E402

from app.ingestion.extract.pdf import PdfExtractor  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=99.0)
    a = ap.parse_args()
    docs = yaml.safe_load(open(DS / "documents.yaml"))["documents"]
    quotes = gold_quotes()
    located = total = 0
    misses: list[tuple[str, str]] = []
    per_doc: dict[str, str] = {}
    for doc_id, meta in docs.items():
        path = ROOT / meta["file"]
        qs = [q for q in quotes.get(doc_id, []) if q]
        if not qs or path.suffix.lower() != ".pdf":
            continue
        t0 = time.time()
        # Table cell separators (` | `) are synthetic formatting, not document text: compare without them.
        texts = [
            norm(p.text.replace(" | ", " ").replace("| ", " ").replace(" |", " "))
            for p in PdfExtractor().extract(str(path)).pages
        ]
        hit = 0
        for q in qs:
            nq = norm(REF.sub("", q))[:300]
            if max(fuzz.partial_ratio(nq, t) for t in texts) >= FUZZ_MIN:
                hit += 1
            else:
                misses.append((doc_id, q[:70]))
        located, total = located + hit, total + len(qs)
        per_doc[doc_id] = f"{hit}/{len(qs)} ({time.time() - t0:.1f}s)"
    pct = 100.0 * located / total if total else 0.0
    print(
        json.dumps(
            {"located": located, "total": total, "located_pct": round(pct, 1), "per_doc": per_doc}, indent=1
        )
    )
    for d, q in misses:
        print(f"miss {d}: {q!r}")
    print(f"located_pct {pct:.1f}")
    return 0 if pct >= a.min else 1


if __name__ == "__main__":
    sys.exit(main())
