"""Every located gold quote maps to >= 1 chunk of the ingested version (chunker-independent gold).

`test_all_gold_mapped` ingests every corpus document whose file exists (slow: full 10-K/10-Q filings), so it
is opt-in like the C1 check: `VAULT_CORPUS=1` (plus `VAULT_CORPUS_ROOT=<checkout with eval/sources/>`
when running from a worktree). `test_authored_gold_mapped` always runs, on the three authored
documents in git.
Run with `-s` to see the coverage table.
"""

import os
import pathlib
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import yaml
from eval.loader import DATASET_DIR, load_dataset
from eval.mapping import ChunkInfo, GoldTarget, map_item, norm
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support.evalcorpus import ingest, source_path
from tests.support.evalkit import REPO_ROOT

DOCS: dict[str, dict[str, Any]] = yaml.safe_load((DATASET_DIR / "documents.yaml").read_text())["documents"]
AUTHORED = {k: v for k, v in DOCS.items() if v["source"].startswith("authored")}


def _check_and_report(chunks: dict[str, list[ChunkInfo]], label: str) -> None:
    ds = load_dataset()
    by_chunk = {c.id: c for cs in chunks.values() for c in cs}
    per_source: dict[str, list[int]] = defaultdict(
        lambda: [0] * 6
    )  # located, mapped, exact-verified, off-page, fuzzy, >1 chunk
    unmapped: list[tuple[str, str, str]] = []
    n_items = 0
    for it in ds.items:
        if not it.referenced_docs() <= set(chunks):
            continue  # a scope document was not ingested in this run
        golds = list(it.gold) + [g for t in it.turns or [] for g in t.gold]
        if not golds:
            continue
        n_items += 1
        targets: list[GoldTarget] = map_item(it, chunks)
        assert len(targets) == len(golds)
        for g, t in zip(golds, targets, strict=True):
            row = per_source[it.source]
            if g.match == "not_found":
                continue
            row[0] += 1
            if not t.mapped:
                unmapped.append((it.id, t.doc_id, g.quote[:70]))
                continue
            row[1] += 1
            for h in t.hits:
                c = by_chunk[h.chunk_id]
                assert c.doc_id == t.doc_id, (it.id, "hit in another document")
                assert 0 <= h.sentence_start < h.sentence_end <= len(c.sentences), (it.id, h)
            row[3] += int(not any(h.on_page for h in t.hits))
            row[4] += int(any(h.match == "fuzzy" for h in t.hits))
            row[5] += int(len(t.hits) > 1)
            if all(h.match == "exact" for h in t.hits) and len(g.quote) <= 300:
                # independent check of the sentence range: the quote lies inside the text of those sentences
                ok = any(
                    norm(g.quote).replace(" | ", " ")[:60]
                    in norm(
                        " ".join(c.text[a:b] for a, b in c.sentences[h.sentence_start : h.sentence_end])
                    ).replace(" | ", " ")
                    for h in t.hits
                    for c in [by_chunk[h.chunk_id]]
                )
                row[2] += int(ok)
    print(
        f"\n[{label}] gold mapping coverage over {n_items} items with gold, "
        f"{sum(map(len, chunks.values()))} chunks"
    )
    for src, (loc, mp, ver, off, fz, multi) in sorted(per_source.items()):
        print(
            f"  {src:13s} mapped {mp}/{loc}  fuzzy {fz}  multi-chunk {multi}  "
            f"exact-range-verified {ver}  off-page {off}"
        )
    tot_l, tot_m = sum(r[0] for r in per_source.values()), sum(r[1] for r in per_source.values())
    print(f"  TOTAL mapped {tot_m}/{tot_l}")
    for u in unmapped:
        print("  UNMAPPED", u)
    assert not unmapped, f"{len(unmapped)} located gold quotes did not map to any chunk"
    assert tot_l > 0
    assert all(r[2] > 0 for r in per_source.values() if r[1] > 0), "no exact range verified for a source"


async def test_authored_gold_mapped(
    api: httpx.AsyncClient,
    make_owner: Callable[[], Any],
    sm: async_sessionmaker[AsyncSession],
    store: Any,
    enqueued: Any,
    tmp_path: pathlib.Path,
) -> None:
    owner = await make_owner()
    chunks, _ = await ingest(api, owner, sm, store, enqueued, tmp_path, AUTHORED)
    _check_and_report(chunks, "authored documents")


@pytest.mark.skipif(not os.environ.get("VAULT_CORPUS"), reason="opt-in: VAULT_CORPUS=1 (slow, full corpus)")
async def test_all_gold_mapped(
    api: httpx.AsyncClient,
    make_owner: Callable[[], Any],
    sm: async_sessionmaker[AsyncSession],
    store: Any,
    enqueued: Any,
    tmp_path: pathlib.Path,
) -> None:
    present = {k: v for k, v in DOCS.items() if source_path(v) is not None}
    if len(present) == len(AUTHORED):
        pytest.skip("eval sources not fetched (make eval-fetch) and VAULT_CORPUS_ROOT not set")
    owner = await make_owner()
    chunks, _ = await ingest(api, owner, sm, store, enqueued, tmp_path, present)
    _check_and_report(chunks, f"corpus: {len(present)}/{len(DOCS)} documents, root {REPO_ROOT.name}")
