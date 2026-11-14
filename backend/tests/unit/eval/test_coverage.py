"""Authored strata 7-14 (items and fixtures), coverage check, anti-leak, injection variants."""

import pathlib
from collections import Counter
from typing import Any

import pytest
import yaml
from eval.build.check_coverage import (
    INJECTION_VARIANTS,
    TARGETS,
    check,
    fixture_sentences,
    longest_shared_run,
    variant_of,
)
from eval.loader import DATASET_V2_DIR, load_dataset_v2, validate_items_v2
from eval.schema import GoldItemV2

AUTHORED = DATASET_V2_DIR / "authored"
DOCS = DATASET_V2_DIR / "docs"
AUTHORED_STRATA = (
    "compare_docs",
    "conflict",
    "aggregation",
    "unanswerable",
    "false_premise",
    "versioned",
    "injection",
    "multi_turn",
)


def _raw() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in sorted(AUTHORED.glob("*.yaml")):
        out += (yaml.safe_load(f.read_text()) or {}).get("items", [])
    return out


def _items() -> list[GoldItemV2]:
    return list(validate_items_v2(_raw()).items)


def _fixture(key: str) -> pathlib.Path | None:
    hits = sorted(p for p in DOCS.glob(f"{key}.*") if p.suffix in {".md", ".docx"})
    return hits[0] if hits else None


def _item(**over: Any) -> GoldItemV2:
    d: dict[str, Any] = {
        "id": "x",
        "stratum": "single_span",
        "question": "q?",
        "source_doc": "d",
        "answer_type": "answer",
        "required_claims": ["c"],
    }
    d.update(over)
    return GoldItemV2.model_validate(d)


def test_authored_items_load() -> None:
    items = _items()
    assert items, "no authored items"
    assert len(load_dataset_v2().items) >= len(items)
    for item in items:
        assert item.stratum in AUTHORED_STRATA


def test_authored_counts_meet_matrix() -> None:
    n = Counter(i.stratum for i in _items())
    for s in AUTHORED_STRATA:
        assert n[s] >= TARGETS[s], f"{s}: {n[s]} < {TARGETS[s]}"


def test_injection_variants() -> None:
    assert len(INJECTION_VARIANTS) == len(set(INJECTION_VARIANTS)) == 11
    tags = [variant_of(i) for i in _items() if i.stratum == "injection"]
    assert None not in tags, "every injection item needs an audit_note starting 'variant=<tag>;'"
    assert set(tags) <= set(INJECTION_VARIANTS)
    assert set(INJECTION_VARIANTS) <= set(tags), set(INJECTION_VARIANTS) - set(tags)


def test_absent_items_explain_why() -> None:
    for i in _items():
        if i.stratum in ("unanswerable", "false_premise"):
            assert i.audit_note and len(i.audit_note.split()) >= 4, i.id
        if i.stratum == "unanswerable":
            assert i.answer_type == "abstain" and not i.gold_evidence


def _leaks_and_ranges() -> int:
    checked = 0
    for i in _items():
        sents: dict[str, list[str]] = {}
        for g in i.gold_evidence:
            path = _fixture(g.doc)
            if path is None:
                continue  # a document of another lane (public / migrated items)
            sents.setdefault(g.doc, fixture_sentences(path))
            assert g.sentence_end <= len(sents[g.doc]), f"{i.id}: range outside {g.doc}"
            for s in sents[g.doc][g.sentence_start : g.sentence_end]:
                for q in [i.question, *i.prior_turns]:
                    run = longest_shared_run(q, s)
                    assert run < 6, f"{i.id}: {run}-word run shared between {q!r} and {s!r}"
                checked += 1
    return checked


def test_no_lexical_leak() -> None:
    """No authored question shares a run of 6+ words with its gold sentences; the measure catches a copy."""
    gold = "Either party may terminate this Agreement on ninety (90) days written notice."
    assert longest_shared_run("Can either party terminate this Agreement on notice?", gold) < 6
    assert longest_shared_run("Either party may terminate this agreement on ninety days", gold) >= 6
    assert _leaks_and_ranges() > 0


def test_injection_items_scoped_and_marked() -> None:
    for i in _items():
        if i.stratum == "injection":
            assert i.answer_type == "answer" and i.doc_ids and i.must_not_contain, i.id


def test_documents_exist_and_are_licence_free() -> None:
    manifest = yaml.safe_load((DOCS / "manifest.yaml").read_text())["documents"]
    for key, meta in manifest.items():
        assert (DOCS / meta["file"]).exists(), key
        assert meta["license"] == "CC0 (authored)"
    keys = set(manifest)
    for i in _items():
        used = {i.source_doc, *(i.doc_ids or []), *(g.doc for g in i.gold_evidence)}
        assert used <= keys, f"{i.id}: unknown documents {used - keys}"


def test_check_coverage() -> None:
    items = [_item(id=f"a{n}", source_doc=f"d{n // 3}", stratum="conflict") for n in range(9)]
    _, problems = check(items)
    assert any("stratum conflict: 9 items" in p for p in problems)  # 9 < 12 = 80% of 15
    assert not any("document" in p for p in problems)
    more = [_item(id=f"b{n}", source_doc=f"e{n}", stratum="conflict") for n in range(3)]
    lines, problems = check(items + more)
    assert problems == []  # 12 = exactly 80%; strata with no items are skipped
    assert any(ln.startswith("conflict") and "ok" in ln for ln in lines)
    _, problems = check([*items, _item(id="z", source_doc="d0", stratum="conflict")])
    assert any("document d0: 4 items" in p for p in problems)
    _, problems = check(items, require_all=True)
    assert any("stratum single_span: 0 items" in p for p in problems)
    assert any("total 9 items outside 280-320" in p for p in problems)
    dropped = _item(id="dr", source_doc="d0", stratum="conflict", audit_status="dropped", audit_note="dup")
    _, problems = check([*items, dropped])
    assert not any("document d0" in p for p in problems)  # dropped items do not count


@pytest.mark.parametrize("a,b,n", [("a b c d", "x b c d y", 3), ("", "a", 0), ("A, b!", "a b", 2)])
def test_longest_shared_run(a: str, b: str, n: int) -> None:
    assert longest_shared_run(a, b) == n


# keep the pathlib import used for type clarity in helpers
_ = pathlib
