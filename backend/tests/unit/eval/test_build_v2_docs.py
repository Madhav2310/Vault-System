"""S21.10a: the committed v2 document map covers every document the live items name, and its split lists are
the ones `splits_v2` computes. Offline and $0 (public PDFs are not needed: only the map is checked)."""

import hashlib
import pathlib

import pytest
from eval.build import build_v2_docs as B
from eval.loader import load_dataset_v2
from eval.splits_v2 import SPLITS_V2, live_items, select_v2


def test_map_covers_every_live_item_document() -> None:
    items = live_items(load_dataset_v2().items)
    docs = B.load_documents_v2()
    assert B.all_doc_keys(items) <= set(docs)
    assert {d["kind"] for d in docs.values()} <= {"authored", "v1", "public", "virtual"}
    assert [k for k, d in docs.items() if d["kind"] == "virtual"] == ["vault"]  # the only cluster-only key


def test_authored_and_v1_files_exist_with_pinned_hash() -> None:
    for key, e in B.load_documents_v2().items():
        if e["kind"] in ("authored", "v1"):
            path = B.ROOT / e["file"]
            if path.is_file():  # v1 third-party PDFs live in the gitignored sources
                assert hashlib.sha256(path.read_bytes()).hexdigest() == e["sha256"], key


def test_split_lists_match_splits_v2() -> None:
    items = load_dataset_v2().items
    docs = B.load_documents_v2()
    want = B.build_splits(items, docs)
    import yaml

    got = yaml.safe_load(B.DOCS_YAML.read_text())["splits"]
    assert got == want, "documents.yaml is stale: run make eval-v2-docs"
    for s in SPLITS_V2:
        assert got[s]["items"] == len(select_v2(items, s))
    # a source_doc appears in exactly one split
    seen = [d for s in SPLITS_V2 for d in got[s]["source_docs"]]
    assert len(seen) == len(set(seen))


def test_sources_prefix_follows_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("VAULT_EVAL_SOURCES", str(tmp_path))
    assert B.resolve_file("eval/sources/fb_pdfs/x.pdf") == tmp_path / "fb_pdfs" / "x.pdf"
    assert (
        B.resolve_file("eval/datasets/vault_eval_v2/docs/a.md")
        == B.ROOT / "eval/datasets/vault_eval_v2/docs/a.md"
    )


def test_missing_files_reports_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("VAULT_EVAL_SOURCES", str(tmp_path))
    docs = {
        "a": {"kind": "public", "file": "eval/sources/fb_pdfs/a.pdf"},
        "v": {"kind": "virtual"},
        "ok": {"kind": "authored", "file": "eval/datasets/vault_eval_v2/docs/manifest.yaml"},
    }
    miss = B.missing_files(["a", "v", "ok", "ghost"], docs)
    assert set(miss) == {"a", "ghost"} and "download" in miss["a"] and "documents.yaml" in miss["ghost"]


def test_authored_units_follow_the_fixture_convention() -> None:
    docs = B.load_documents_v2()
    units = B.document_units("msa_northwind", docs["msa_northwind"])
    assert units and all(isinstance(u, str) and u for u in units)
    assert B.document_units("vault", docs["vault"]) is None
