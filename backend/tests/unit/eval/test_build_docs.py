"""The committed document map covers every document the live items name, and its split lists are the
ones `eval.splits` computes. Offline and $0 (public PDFs are not needed: only the map is checked)."""

import hashlib
import pathlib

import pytest
from eval.build import build_docs as B
from eval.loader import load_dataset
from eval.splits import SPLITS, live_items, select_split


def test_map_covers_every_live_item_document() -> None:
    items = live_items(load_dataset().items)
    docs = B.load_documents()
    assert B.all_doc_keys(items) <= set(docs)
    assert {d["kind"] for d in docs.values()} <= {"authored", "public", "virtual"}
    assert [k for k, d in docs.items() if d["kind"] == "virtual"] == ["vault"]  # the only cluster-only key


def test_authored_files_exist_with_pinned_hash() -> None:
    authored = {k: e for k, e in B.load_documents().items() if e["kind"] == "authored"}
    assert {"auth_amendment", "auth_side_letter", "auth_travel_policy"} <= set(authored)
    for key, e in authored.items():
        path = B.ROOT / e["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == e["sha256"], key


def test_named_sources_map_to_their_files() -> None:
    import yaml

    named = yaml.safe_load(B.NAMED_SOURCES.read_text())["documents"]
    docs = B.load_documents()
    for key, e in named.items():
        assert docs[key]["kind"] == "public" and docs[key]["file"] == e["file"], key


def test_split_lists_match_the_splits() -> None:
    items = load_dataset().items
    docs = B.load_documents()
    want = B.build_splits(items, docs)
    import yaml

    got = yaml.safe_load(B.DOCS_YAML.read_text())["splits"]
    assert got == want, "documents.yaml is stale: run make eval-docs"
    for s in SPLITS:
        assert got[s]["items"] == len(select_split(items, s))
    # a source_doc appears in exactly one split
    seen = [d for s in SPLITS for d in got[s]["source_docs"]]
    assert len(seen) == len(set(seen))


def test_sources_prefix_follows_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("VAULT_EVAL_SOURCES", str(tmp_path))
    assert B.resolve_file("eval/sources/fb_pdfs/x.pdf") == tmp_path / "fb_pdfs" / "x.pdf"
    assert (
        B.resolve_file("eval/datasets/vault_eval/docs/a.md") == B.ROOT / "eval/datasets/vault_eval/docs/a.md"
    )


def test_missing_files_reports_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("VAULT_EVAL_SOURCES", str(tmp_path))
    docs = {
        "a": {"kind": "public", "file": "eval/sources/fb_pdfs/a.pdf"},
        "v": {"kind": "virtual"},
        "ok": {"kind": "authored", "file": "eval/datasets/vault_eval/docs/manifest.yaml"},
    }
    miss = B.missing_files(["a", "v", "ok", "ghost"], docs)
    assert set(miss) == {"a", "ghost"} and "download" in miss["a"] and "documents.yaml" in miss["ghost"]


def test_authored_units_follow_the_fixture_convention() -> None:
    docs = B.load_documents()
    units = B.document_units("msa_northwind", docs["msa_northwind"])
    assert units and all(isinstance(u, str) and u for u in units)
    assert B.document_units("vault", docs["vault"]) is None
