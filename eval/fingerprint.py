"""Run fingerprints: what a sidecar must share with another sidecar for a paired comparison to mean anything.

A fingerprint is a plain JSON object stored in `<run_id>.json` under `fingerprint`:

    dataset  {id, split, gold_sha, n_gold}   hash of the loaded live items of the split
    items    {n, ids, sha}                   the sorted item ids that were evaluated, and their hash
    filter   the `--filter` spec or null
    judge    {claims, support, abstain}      judge prompt `id@version` (eval.judge.PROMPTS_V2)
    answer   {prompts, models, retrieval, switches, index}  as the server recorded it
    code     {git_sha, dirty}                the eval code that ran

`differences` lists every field that differs; `eval.paired` refuses unless each is named in `--allow`.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel

KNOWN_PATHS = frozenset(
    {
        "dataset",
        "dataset.id",
        "dataset.split",
        "dataset.gold_sha",
        "dataset.n_gold",
        "items",
        "items.n",
        "items.ids",
        "items.sha",
        "filter",
        "judge",
        "answer",
        "answer.prompts",
        "answer.models",
        "answer.retrieval",
        "answer.switches",
        "answer.index",
        "code",
        "code.git_sha",
        "code.dirty",
    }
)
# `--allow items` is for filtered arms: the filter that produced the subset may differ with it.
IMPLIES = {"items": ("filter",)}
UNAVAILABLE = "answer.unavailable"
ALIASES = {
    "retrieval": "answer.retrieval",
    "prompts": "answer.prompts",
    "models": "answer.models",
}


def normalize_allow(allow: Iterable[str]) -> list[str]:
    return [ALIASES.get(a, a) for a in allow]


def digest(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def gold_digest(items: Iterable[BaseModel]) -> tuple[str, int]:
    """(hash, count) of the content of the given gold items, order-independent."""
    dumped = sorted((i.model_dump(mode="json") for i in items), key=lambda d: str(d.get("id")))
    return digest(dumped), len(dumped)


def code_version(cwd: pathlib.Path | None = None) -> dict[str, Any]:
    """Git sha of the eval code and whether tracked files differ from it."""
    where = cwd or pathlib.Path(__file__).parent

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=where,
        ).stdout.strip()

    try:
        return {
            "git_sha": git("rev-parse", "--short", "HEAD") or "unknown",
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"git_sha": "unknown", "dirty": None}


def judge_versions(prompts: Mapping[str, tuple[str, int]]) -> dict[str, str]:
    return {k: f"{n}@{v}" for k, (n, v) in sorted(prompts.items())}


def merge_answer_config(
    run_configs: Sequence[Mapping[str, Any]], index: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """One answer-side config from the `rag_runs.config` rows of a run's turns (server-recorded).

    `prompts` is the sorted union (a compare turn and a plain turn name different answer prompts), `models`
    maps role to the model, `retrieval` is the config version, `switches` the chat_* settings. A value that
    differs between turns of one run is kept as `a|b`, so two runs agree only when every turn agreed.
    """
    prompts = sorted({p for c in run_configs for p in c.get("prompts", [])})
    models: dict[str, set[str]] = {}
    switches: dict[str, set[str]] = {}
    for c in run_configs:
        for role, m in (c.get("models") or {}).items():
            models.setdefault(role, set()).add(str(m))
        for k, v in (c.get("switches") or {}).items():
            switches.setdefault(k, set()).add(json.dumps(v))
    retrieval = sorted({str(c["retrieval"]) for c in run_configs if "retrieval" in c})
    return {
        "prompts": prompts,
        "models": {k: "|".join(sorted(v)) for k, v in sorted(models.items())},
        "retrieval": "|".join(retrieval),
        "switches": {k: "|".join(sorted(v)) for k, v in sorted(switches.items())},
        "index": dict(index or {}),
    }


def build(
    *,
    dataset_id: str,
    split: str,
    split_items: Iterable[BaseModel],
    evaluated_ids: Iterable[str],
    filter_spec: str | None,
    judge: Mapping[str, str],
    answer: Mapping[str, Any] | None,
    code: Mapping[str, Any],
) -> dict[str, Any]:
    sha, n_gold = gold_digest(split_items)
    ids = sorted(evaluated_ids)
    return {
        "dataset": {
            "id": dataset_id,
            "split": split,
            "gold_sha": sha,
            "n_gold": n_gold,
        },
        "items": {"n": len(ids), "ids": ids, "sha": digest(ids)},
        "filter": filter_spec,
        "judge": dict(judge),
        "answer": dict(answer) if answer else {"unavailable": True},
        "code": dict(code),
    }


def flatten(fp: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in fp.items():
        path = f"{prefix}{k}"
        if isinstance(v, Mapping) and v:
            out.update(flatten(v, f"{path}."))
        else:
            out[path] = v
    return out


def _covered(path: str, allow: Iterable[str]) -> bool:
    names = set(normalize_allow(allow))
    for a in list(names):
        names.update(IMPLIES.get(a, ()))
    return any(path == a or path.startswith(a + ".") for a in names)


def unknown_allowed(a: Mapping[str, Any], b: Mapping[str, Any], allow: Iterable[str]) -> list[str]:
    known = set(KNOWN_PATHS) | set(flatten(a)) | set(flatten(b))
    known |= {p.rsplit(".", 1)[0] for p in list(known) if "." in p}
    return sorted(x for x in normalize_allow(allow) if x not in known)


def differences(
    a: Mapping[str, Any], b: Mapping[str, Any], allow: Iterable[str] = ()
) -> list[tuple[str, Any, Any]]:
    """Fields (dotted path, A value, B value) that differ and are not covered by `allow`."""
    fa, fb = flatten(a), flatten(b)
    out = []
    for path in sorted(set(fa) | set(fb)):
        va, vb = fa.get(path), fb.get(path)
        if path == UNAVAILABLE:
            if va or vb:  # no server-reported config: two such runs are not known to agree
                out.append(
                    (
                        "answer",
                        "not server-reported" if va else "reported",
                        "not server-reported" if vb else "reported",
                    )
                )
            continue
        if va != vb:
            out.append((path, va, vb))
    return [d for d in out if not _covered(d[0], allow)]


def _short(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 160 else s[:157] + "..."


def render_differences(diffs: Sequence[tuple[str, Any, Any]]) -> str:
    lines = [f"{'field':<24} {'A':<40} B"]
    lines += [f"{p:<24} {_short(va):<40} {_short(vb)}" for p, va, vb in diffs]
    return "\n".join(lines)
