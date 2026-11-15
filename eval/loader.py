"""Load and validate the golden set (`eval/datasets/vault_eval`).

Items live in part files (`parts/*.yaml`, `authored/*.yaml`), each with a top-level `items:` list. They are
validated together, so the per-document cap spans parts. `DatasetError` lists every problem found (it does
not stop at the first), so a bad edit is diagnosed in one run.
"""

from __future__ import annotations

import pathlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import ValidationError

from eval.schema import GoldItem

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "eval" / "datasets" / "vault_eval"
DATASET_ID = "vault_eval"
MAX_ITEMS_PER_DOC = 3  # design effect about 1.6


class DatasetError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} dataset problem(s):\n  " + "\n  ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Dataset:
    items: tuple[GoldItem, ...]

    def by_id(self) -> dict[str, GoldItem]:
        return {i.id: i for i in self.items}


def validate_items(raw: list[Any]) -> Dataset:
    """Validate raw items; raises `DatasetError` listing every problem (item level, then dataset level).

    The per-document cap counts every item with its `source_doc` except `audit_status: dropped` ones."""
    problems: list[str] = []
    items: list[GoldItem] = []
    for n, r in enumerate(raw):
        try:
            items.append(GoldItem.model_validate(r))
        except ValidationError as e:
            name = r.get("id", f"#{n}") if isinstance(r, dict) else f"#{n}"
            problems.append(
                f"{name}: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            )
    problems += [f"duplicate item id {k}" for k, c in Counter(i.id for i in items).items() if c > 1]
    per_doc: dict[str, list[str]] = {}
    for i in items:
        if i.audit_status != "dropped":
            per_doc.setdefault(i.source_doc, []).append(i.id)
    for doc, ids in sorted(per_doc.items()):
        if len(ids) > MAX_ITEMS_PER_DOC:
            problems.append(
                f"source_doc {doc} has {len(ids)} items (max {MAX_ITEMS_PER_DOC}): "
                f"{', '.join(ids[MAX_ITEMS_PER_DOC:])} exceed the cap"
            )
    if problems:
        raise DatasetError(problems)
    return Dataset(tuple(items))


def part_files(root: pathlib.Path = DATASET_DIR) -> list[pathlib.Path]:
    return [*sorted((root / "parts").glob("*.yaml")), *sorted((root / "authored").glob("*.yaml"))]


def load_dataset(root: pathlib.Path = DATASET_DIR) -> Dataset:
    raw: list[dict[str, Any]] = []
    for f in part_files(root):
        raw += (yaml.safe_load(f.read_text()) or {}).get("items", [])
    return validate_items(raw)
