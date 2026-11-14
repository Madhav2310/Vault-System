"""Deterministic v2 splits: train / dev / test by a hash of `source_doc`, 40 / 20 / 40.

The unit of assignment is the cluster key (`source_doc`), so no document is in two splits and every item of a
document travels together. The hash is sha256 of the key (no salt, no randomness): the same key lands in the
same split on every machine and every run, whatever else is in the dataset. Splits are therefore
approximate in item count (about 40 / 20 / 40 of the documents); `split_counts` prints the realised sizes.

Caveat: an item may also name other documents in `doc_ids` / `gold_evidence` (compare, conflict, versioned
families). Those documents are read at run time but the item stays in its `source_doc`'s split.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Sequence

from eval.schema import GoldItemV2

SPLITS_V2 = ("train", "dev", "test")
SPLIT_PERCENT = {"train": 40, "dev": 20, "test": 40}  # cumulative bounds: train < 40 <= dev < 60 <= test


def bucket(source_doc: str) -> int:
    """0..99, stable across processes (not Python's salted `hash`)."""
    return int.from_bytes(hashlib.sha256(source_doc.encode()).digest()[:8], "big") % 100


def split_of(source_doc: str) -> str:
    b = bucket(source_doc)
    if b < SPLIT_PERCENT["train"]:
        return "train"
    if b < SPLIT_PERCENT["train"] + SPLIT_PERCENT["dev"]:
        return "dev"
    return "test"


def live_items(items: Iterable[GoldItemV2]) -> list[GoldItemV2]:
    """Items that run: `audit_status: dropped` never does."""
    return [i for i in items if i.audit_status != "dropped"]


def select_v2(items: Iterable[GoldItemV2], split: str = "all") -> list[GoldItemV2]:
    """Live items of one split (`all` = every live item), in input order."""
    if split != "all" and split not in SPLITS_V2:
        raise ValueError(f"unknown v2 split {split!r} (use {', '.join(SPLITS_V2)} or all)")
    return [i for i in live_items(items) if split == "all" or split_of(i.source_doc) == split]


def select_dropped_v2(items: Iterable[GoldItemV2], split: str = "all") -> list[GoldItemV2]:
    """The `audit_status: dropped` items of one split, with their stored gold as is (`--only-dropped` runs)."""
    if split != "all" and split not in SPLITS_V2:
        raise ValueError(f"unknown v2 split {split!r} (use {', '.join(SPLITS_V2)} or all)")
    return [
        i for i in items if i.audit_status == "dropped" and (split == "all" or split_of(i.source_doc) == split)
    ]


def split_counts(items: Sequence[GoldItemV2]) -> dict[str, tuple[int, int]]:
    """split -> (live items, distinct source_docs)."""
    live = live_items(items)
    n = Counter(split_of(i.source_doc) for i in live)
    docs: dict[str, set[str]] = {s: set() for s in SPLITS_V2}
    for i in live:
        docs[split_of(i.source_doc)].add(i.source_doc)
    return {s: (n[s], len(docs[s])) for s in SPLITS_V2}
