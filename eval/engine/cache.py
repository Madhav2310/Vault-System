"""Stage cache (research 06 §4): results keyed by `(item, stage-config hash, prompt@v, model)`.

The key is the sha256 of a canonical JSON document holding the item id and content digest, the stage name,
the stage's own `StageKey` (config hash, prompt@version, model), the repetition (only for stages that are
per-rep) and the cache keys of every upstream stage. The upstream chain is what makes invalidation
stage-ordered: a new answer prompt changes the generation key and, through the chain, the judge keys, while
the retrieval key (which does not depend on it) still hits.

Values are JSON files `<root>/<key[:2]>/<key>.json` holding `{"schema", "stage", "key", "value"}`, written
atomically. A missing, corrupt or older-schema file is a miss, never an error.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from eval.engine.state import ROOT, atomic_write_text

CACHE_DIR = ROOT / "eval" / ".cache"
CACHE_SCHEMA = 1

__all__ = ["CACHE_DIR", "CACHE_SCHEMA", "CacheEntry", "StageCache", "StageKey", "canonical_json", "digest"]


def canonical_json(obj: Any) -> str:
    """Byte-stable JSON: sorted keys, no whitespace, no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StageKey:
    """What a stage declares about itself. `config` is any JSON-able description of the stage's settings
    (it is hashed); `prompt` is `"<id>@<version>"` for stages that use a prompt; `model` is the resolved
    model name (not the role) for stages that call a model."""

    config: Any = None
    prompt: str | None = None
    model: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {"config": digest(self.config), "prompt": self.prompt, "model": self.model}


def cache_key(
    *,
    item_id: str,
    item_digest: str,
    stage: str,
    key: StageKey,
    rep: int | None,
    upstream: Mapping[str, str],
) -> str:
    return digest(
        {
            "schema": CACHE_SCHEMA,
            "item": item_id,
            "item_digest": item_digest,
            "stage": stage,
            "stage_key": key.as_json(),
            "rep": rep,
            "upstream": dict(upstream),
        }
    )


@dataclass(frozen=True)
class CacheEntry:
    value: Any  # may legitimately be None, hence the wrapper


class StageCache:
    def __init__(self, root: pathlib.Path = CACHE_DIR) -> None:
        self.root = root
        self.hits: Counter[str] = Counter()
        self.misses: Counter[str] = Counter()

    def _path(self, key: str) -> pathlib.Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str, stage: str) -> CacheEntry | None:
        try:
            doc = json.loads(self._path(key).read_text(encoding="utf-8"))
            ok = doc["schema"] == CACHE_SCHEMA and doc["stage"] == stage
        except (OSError, ValueError, KeyError, TypeError):
            ok, doc = False, None
        if not ok:
            self.misses[stage] += 1
            return None
        self.hits[stage] += 1
        return CacheEntry(doc["value"])

    def put(self, key: str, stage: str, value: Any) -> None:
        atomic_write_text(
            self._path(key),
            canonical_json({"schema": CACHE_SCHEMA, "stage": stage, "key": key, "value": value}),
        )
