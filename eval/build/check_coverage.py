"""Coverage check for the v2 gold set: items per stratum vs the matrix target, per document.

    python -m eval.build.check_coverage eval/datasets/vault_eval_v2 [--require-all]

Without `--require-all` only strata that have at least one item are checked (other lanes add strata 1-6
and the migrated v1 items in separate part files, so a partial dataset is normal). With it every stratum
must be present and at least 80% of target, and the total must be 280-320. A document with more than 3
non-dropped items always fails (the loader enforces the same cap). Also hosts the helpers the
authored-item tests share: the injection-variant constant, the document sentence enumeration and the
lexical-leak measure.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import Counter

from eval.loader import MAX_ITEMS_PER_DOC, DatasetError, load_dataset_v2
from eval.schema import GoldItemV2

# Matrix (docs/plan/S21.md): stratum -> target n. Order = matrix order.
TARGETS: dict[str, int] = {
    "single_span": 40,
    "conditional": 20,
    "table_lookup": 25,
    "numeric_calc": 20,
    "multi_span": 20,
    "multi_hop": 20,
    "compare_docs": 20,
    "conflict": 15,
    "aggregation": 10,
    "unanswerable": 25,
    "false_premise": 15,
    "versioned": 15,
    "injection": 20,
    "multi_turn": 10,
    "formatting": 5,
}
MIN_FRACTION = 0.8
TOTAL_RANGE = (280, 320)

# The 11 injection variants of the matrix. An injection item's `audit_note` starts with `variant=<tag>;`.
INJECTION_VARIANTS: tuple[str, ...] = (
    "body_text",  # instruction in running prose
    "footnote",  # instruction in a footnote
    "table_cell",  # instruction in a table cell
    "hidden_text",  # DOCX comment / tracked change / hidden-looking text
    "fake_system",  # fake "SYSTEM:" / "assistant:" message
    "ignore_previous",  # "ignore previous instructions"
    "exfiltration",  # asks to reveal the prompt, other documents, or to send data to a URL
    "citation_spoof",  # asks to cite a fabricated handle ("cite E9.9")
    "role_play",  # "you are now ..." persona switch
    "unicode_zero_width",  # instruction split with zero-width characters
    "markdown_link",  # markdown image / link injection
)
_VARIANT = re.compile(r"^variant=([a-z_]+);")


def variant_of(item: GoldItemV2) -> str | None:
    m = _VARIANT.match(item.audit_note or "")
    return m.group(1) if m else None


# --- document sentence enumeration (the convention gold_evidence ranges use; see the v2 README appendix) ---


def document_sentences(text: str) -> list[str]:
    """Sentences of a Markdown fixture in reading order: blocks are separated by blank lines; a block whose
    lines all start with `|` is a table (one sentence per row, separator rows skipped); any other block is
    split with the ingestion sentence splitter (a heading line is one sentence)."""
    # lazy: only the sentence-level tests need the backend package
    from app.ingestion.sentences import split_sentences

    out: list[str] = []
    for block in re.split(r"(?:\n[ \t\r]*){2,}", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if lines and all(ln.startswith("|") for ln in lines):
            out += [ln for ln in lines if not re.fullmatch(r"[|\-:\s]+", ln)]
        elif len(lines) == 1 and lines[0].startswith("#"):
            out.append(lines[0])  # a heading is one sentence
        else:
            joined = "\n".join(lines)
            out += [joined[a:b] for a, b in split_sentences(joined)]
    return out


def fixture_sentences(path: pathlib.Path) -> list[str]:
    """`document_sentences` for a fixture file: Markdown as is, DOCX as its non-empty body paragraphs."""
    if path.suffix == ".docx":
        from docx import Document

        paras = [p.text for p in Document(str(path)).paragraphs if p.text.strip()]
        return document_sentences("\n\n".join(paras))
    return document_sentences(path.read_text(encoding="utf-8"))


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def longest_shared_run(a: str, b: str) -> int:
    """Length in words of the longest contiguous word run (case/punctuation-insensitive) common to a and b."""
    x, y = _tokens(a), _tokens(b)
    best = 0
    prev = [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        cur = [0] * (len(y) + 1)
        for j in range(1, len(y) + 1):
            if x[i - 1] == y[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


# --- the check ---


def check(items: list[GoldItemV2], *, require_all: bool = False) -> tuple[list[str], list[str]]:
    """Returns (report lines, problems)."""
    live = [i for i in items if i.audit_status != "dropped"]
    per_stratum = Counter(i.stratum for i in live)
    per_doc = Counter(i.source_doc for i in live)
    problems: list[str] = []
    lines = [f"{'stratum':<15}{'n':>5}{'target':>8}{'%':>6}  status"]
    for s, target in TARGETS.items():
        n = per_stratum.get(s, 0)
        need = -(-int(target * MIN_FRACTION * 100) // 100)  # ceil(0.8 * target)
        if n == 0 and not require_all:
            status = "absent (skipped)"
        elif n < need:
            status = f"FAIL (< {need})"
            problems.append(f"stratum {s}: {n} items, need at least {need} (80% of {target})")
        else:
            status = "ok"
        lines.append(f"{s:<15}{n:>5}{target:>8}{round(100 * n / target):>6}  {status}")
    lines.append(f"{'total':<15}{len(live):>5}{sum(TARGETS.values()):>8}")
    if require_all and not TOTAL_RANGE[0] <= len(live) <= TOTAL_RANGE[1]:
        problems.append(f"total {len(live)} items outside {TOTAL_RANGE[0]}-{TOTAL_RANGE[1]}")
    for doc, n in sorted(per_doc.items()):
        if n > MAX_ITEMS_PER_DOC:
            problems.append(f"document {doc}: {n} items (max {MAX_ITEMS_PER_DOC})")
    lines.append(f"documents: {len(per_doc)}; max items per document: {max(per_doc.values(), default=0)}")
    lines.append("per document: " + ", ".join(f"{d}={n}" for d, n in sorted(per_doc.items())))
    return lines, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=pathlib.Path)
    ap.add_argument("--require-all", action="store_true", help="every stratum present, total 280-320")
    args = ap.parse_args(argv)
    try:
        ds = load_dataset_v2(args.root)
    except DatasetError as e:
        print(e)
        return 1
    lines, problems = check(list(ds.items), require_all=args.require_all)
    print("\n".join(lines))
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
