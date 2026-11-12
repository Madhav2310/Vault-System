"""Vault-Eval v1 corpus integrity (plan S0.18 AC2/AC3).

Third-party documents live in the gitignored ``eval/sources/`` (``make eval-fetch``); without them
these tests skip. Gold quotes are re-located independently of the build, page by page, with the
build's own production extraction config and matcher.
"""

import hashlib
import sys
from functools import cache
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
import yaml


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "eval" / "datasets" / "vault_eval_v1" / "documents.yaml").is_file():
            return p
    raise RuntimeError("repo root with eval/datasets/vault_eval_v1 not found")


ROOT = _repo_root()
DS = ROOT / "eval" / "datasets" / "vault_eval_v1"
DOCS: dict[str, dict[str, Any]] = yaml.safe_load((DS / "documents.yaml").read_text())["documents"]
# Items whose grading needs no evidence quote: abstentions, rubric summaries, vault metadata.
NO_EVIDENCE_GRADING = {"abstention", "rubric", "exact_meta", "set_meta"}


@pytest.fixture(scope="module")
def sources() -> None:
    missing = [m["file"] for m in DOCS.values() if not (ROOT / m["file"]).is_file()]
    if missing:
        pytest.skip(f"eval/sources/ not fetched ({len(missing)} documents missing); run `make eval-fetch`")


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_hashes_match(sources: None) -> None:
    mismatched = {d: m["file"] for d, m in DOCS.items() if _sha256(ROOT / m["file"]) != m["sha256"]}
    assert not mismatched
    assert len(DOCS) == 15


@cache
def _build() -> Any:
    sys.path.insert(0, str(ROOT / "eval" / "build"))
    import build_external

    return build_external


@cache
def _page_text(doc_id: str, page: int | None) -> str:
    b = _build()
    path = ROOT / DOCS[doc_id]["file"]
    if page is None:  # authored DOCX / Markdown
        if path.suffix == ".docx":
            from docx import Document

            return str(b.norm("\n".join(par.text for par in Document(str(path)).paragraphs)))
        return str(b.norm(path.read_text()))
    with pdfplumber.open(path) as pdf:
        return str(b.norm(pdf.pages[page - 1].dedupe_chars().extract_text(**b.EXTRACT_CFG) or ""))


def _golds(item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    default = (item.get("doc_ids") or [None])[0]
    gs = item.get("gold", []) + [g for t in item.get("turns", []) for g in t["gold"]]
    return [(g.get("doc_id") or default, g) for g in gs]


def _located(doc_id: str, gold: dict[str, Any]) -> bool:
    if gold.get("match") == "not_found":
        return False
    return _build().locate(gold["quote"], [_page_text(doc_id, gold["page"])])["page"] is not None


def test_gold_located(sources: None) -> None:
    items = yaml.safe_load((DS / "golden.yaml").read_text())["items"]
    assert len(items) == 122
    checked, unlocated = 0, []
    for it in items:
        if it["category"] == "unanswerable" or it["grading"] in NO_EVIDENCE_GRADING:
            continue
        checked += 1
        if not any(_located(d, g) for d, g in _golds(it)):
            unlocated.append(it["id"])
    assert not unlocated
    assert checked == 100
