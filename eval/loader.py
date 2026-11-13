"""Load and validate Vault-Eval v1 (`eval/datasets/vault_eval_v1`): every item, the splits, the documents.

`load_dataset()` raises `DatasetError` listing every problem found (it does not stop at the first), so a bad
edit to golden.yaml is diagnosed in one run. Document-level holdout is checked by `holdout_violations`.
"""

from __future__ import annotations

import pathlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import ValidationError

from eval.schema import GoldenItem, Split

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "eval" / "datasets" / "vault_eval_v1"
EXPECTED_ITEMS = 122
SPLITS: tuple[Split, ...] = ("train", "test", "smoke")


class DatasetError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} dataset problem(s):\n  " + "\n  ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Dataset:
    items: tuple[GoldenItem, ...]
    splits: dict[str, tuple[str, ...]]  # train / test / smoke -> item ids
    holdout_docs: frozenset[str]
    documents: dict[str, dict[str, Any]]  # documents.yaml entries by key

    def by_id(self) -> dict[str, GoldenItem]:
        return {i.id: i for i in self.items}

    def select(self, split: str = "all") -> list[GoldenItem]:
        """Items of one split in golden.yaml order; `all` = every item."""
        if split == "all":
            return list(self.items)
        if split not in self.splits:
            raise KeyError(f"unknown split {split!r}")
        wanted = set(self.splits[split])
        return [i for i in self.items if i.id in wanted]

    def split_of(self, item_id: str) -> str:
        """`train` or `test` (smoke is an overlay on both)."""
        return "train" if item_id in set(self.splits["train"]) else "test"


def load_dataset(root: pathlib.Path = DATASET_DIR, *, expected_items: int | None = EXPECTED_ITEMS) -> Dataset:
    problems: list[str] = []
    raw = yaml.safe_load((root / "golden.yaml").read_text())["items"]
    items: list[GoldenItem] = []
    for n, r in enumerate(raw):
        try:
            items.append(GoldenItem.model_validate(r))
        except ValidationError as e:
            name = r.get("id", f"#{n}") if isinstance(r, dict) else f"#{n}"
            problems.append(
                f"{name}: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            )
    ids = [i.id for i in items]
    problems += [f"duplicate item id {k}" for k, c in Counter(ids).items() if c > 1]
    if expected_items is not None and len(raw) != expected_items:
        problems.append(f"expected {expected_items} items, found {len(raw)}")

    sp = yaml.safe_load((root / "splits.yaml").read_text())
    docs: dict[str, dict[str, Any]] = yaml.safe_load((root / "documents.yaml").read_text())["documents"]
    splits: dict[str, tuple[str, ...]] = {k: tuple(sp[k]) for k in SPLITS}
    known = set(ids)
    for k, v in splits.items():
        problems += [f"splits.{k}: unknown item {i}" for i in v if i not in known]
        problems += [f"splits.{k}: duplicate item {i}" for i, c in Counter(v).items() if c > 1]
    both = set(splits["train"]) & set(splits["test"])
    problems += [f"item {i} is in both train and test" for i in sorted(both)]
    unassigned = known - set(splits["train"]) - set(splits["test"])
    problems += [f"item {i} is in neither train nor test" for i in sorted(unassigned)]
    for i in items:
        problems += [f"{i.id}: unknown document {d}" for d in sorted(i.referenced_docs()) if d not in docs]
    holdout = frozenset(sp["holdout_docs"])
    problems += [f"holdout document {d} is not in documents.yaml" for d in sorted(holdout - set(docs))]

    ds = Dataset(tuple(items), splits, holdout, docs)
    if not problems:
        problems += holdout_violations(ds)
    if problems:
        raise DatasetError(problems)
    return ds


def holdout_violations(ds: Dataset) -> list[str]:
    """Document-level holdout: holdout documents are referenced by test items only (and by at least one),
    so a test item can never depend on content the tuner saw through a train item."""
    by_id = ds.by_id()
    train_docs = {d for i in ds.splits["train"] for d in by_id[i].referenced_docs()}
    test_docs = {d for i in ds.splits["test"] for d in by_id[i].referenced_docs()}
    out: list[str] = []
    for i in ds.splits["train"]:
        bad = by_id[i].referenced_docs() & ds.holdout_docs
        out += [f"train item {i} references holdout document {d}" for d in sorted(bad)]
    out += [f"holdout document {d} appears in train items" for d in sorted(train_docs & ds.holdout_docs)]
    out += [f"holdout document {d} is not used by any test item" for d in sorted(ds.holdout_docs - test_docs)]
    return out
