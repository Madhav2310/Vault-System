"""S21.05: apply the human decisions of `audit_queue.yaml` to the v2 dataset files.

Decision rules (deterministic, idempotent; a second run changes nothing):

  gold_wrong | gold_incomplete   `required_claims` := `fix` (the corrected FULL list); item `verified`
  ambiguous                      item `dropped`, `audit_note` carries the reason (`note`)
  system_wrong                   gold kept as is; item `verified`
  audited and not queued         item `verified` automatically
  not audited (queue `audited`)  untouched, stays `draft`

The tool refuses (exit 2, nothing written) while any queued item has no decision, on an unknown decision, on a
missing/invalid `fix`, on a queued id that is not in the dataset, and on a stale queue (the item's claims are
neither the queue's nor the `fix`). Every change is a surgical line edit of the part file (comments and the
hand formatting of the other items survive); the result is re-parsed and compared with the intended items, and
the whole dataset is re-validated before anything is written.

    python -m eval.build.apply_audit [--queue FILE] [--dataset-dir DIR] [--check]
"""

from __future__ import annotations

import argparse
import copy
import pathlib
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml
from eval.build.audit import DECISIONS, QUEUE_FILE, load_queue
from eval.loader import DATASET_V2_DIR, DatasetError, validate_items_v2
from eval.schema import MAX_REQUIRED_CLAIMS

MARKER = "[S21.05 audit: "
_MARKER_RE = re.compile(r"\s*\[S21\.05 audit: [^\]]*\].*\Z", re.S)
_ITEM_RE = re.compile(r"^(\s*)- id: ")


class ApplyError(Exception):
    """A refusal; the message lists every problem and nothing was written."""

    def __init__(self, problems: Sequence[str]):
        super().__init__("\n".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True)
class Change:
    status: str
    claims: tuple[str, ...] | None  # new required_claims, None = keep
    note: str | None  # new audit_note, None = keep


# --- decisions -> changes ---------------------------------------------------------------------------------


def strip_marker(note: str | None) -> str:
    return _MARKER_RE.sub("", note or "").strip()


def _with_marker(note: str | None, decision: str, why: str | None) -> str:
    base = strip_marker(note)
    tail = f"{MARKER}{decision}]" + (f" {' '.join(why.split())}" if why and why.strip() else "")
    return f"{base} {tail}" if base else tail


def check_queue(queue: Mapping[str, Any], items: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every problem with the queue's decisions against the dataset, as messages (empty = applicable)."""
    problems: list[str] = []
    undecided = [q["id"] for q in queue["queue"] if not q.get("decision")]
    if undecided:
        problems.append(
            f"{len(undecided)} queued item(s) have no decision (refusing to verify): {', '.join(undecided)}"
        )
    for q in queue["queue"]:
        iid, d = q["id"], q.get("decision")
        raw = items.get(iid)
        if raw is None:
            problems.append(f"{iid}: queued but not in the dataset")
            continue
        if not d:
            continue
        if d not in DECISIONS:
            problems.append(f"{iid}: unknown decision {d!r} (one of {', '.join(DECISIONS)})")
            continue
        if d in ("gold_wrong", "gold_incomplete"):
            fix = q.get("fix")
            if raw.get("answer_type") == "abstain":
                problems.append(f"{iid}: abstain items take no claims; decide ambiguous or system_wrong")
            elif (
                not isinstance(fix, list)
                or not 1 <= len(fix) <= MAX_REQUIRED_CLAIMS
                or any(not isinstance(c, str) or not c.strip() for c in fix)
            ):
                problems.append(f"{iid}: {d} needs `fix`: 1 to {MAX_REQUIRED_CLAIMS} non-blank claims")
            elif list(raw.get("required_claims", [])) not in (list(q["required_claims"]), list(fix)):
                problems.append(f"{iid}: stale queue: the item's claims changed since the audit")
        elif list(raw.get("required_claims", [])) != list(q["required_claims"]):
            problems.append(f"{iid}: stale queue: the item's claims changed since the audit")
        if d == "ambiguous" and not (q.get("note") or "").strip():
            problems.append(f"{iid}: ambiguous needs a `note` giving the reason")
    return problems


def plan_changes(queue: Mapping[str, Any], items: Mapping[str, Mapping[str, Any]]) -> dict[str, Change]:
    """item id -> Change. Call after `check_queue` returned no problems."""
    out: dict[str, Change] = {}
    queued = {q["id"]: q for q in queue["queue"]}
    for iid in queue["audited"]:
        raw = items.get(iid)
        if iid in queued or raw is None or raw.get("audit_status") == "dropped":
            continue
        out[iid] = Change("verified", None, None)
    for iid, q in queued.items():
        raw, d = items[iid], q["decision"]
        if d in ("gold_wrong", "gold_incomplete"):
            out[iid] = Change(
                "verified", tuple(q["fix"]), _with_marker(raw.get("audit_note"), d, q.get("note"))
            )
        elif d == "ambiguous":
            out[iid] = Change("dropped", None, _with_marker(raw.get("audit_note"), d, q.get("note")))
        else:
            out[iid] = Change("verified", None, _with_marker(raw.get("audit_note"), d, q.get("note")))
    return out


def expected_raw(raw: Mapping[str, Any], ch: Change) -> dict[str, Any]:
    new = copy.deepcopy(dict(raw))
    new["audit_status"] = ch.status
    if ch.claims is not None:
        new["required_claims"] = list(ch.claims)
        alts = {
            k: v
            for k, v in (new.get("alternates") or {}).items()
            if k in set(ch.claims) | set(new.get("optional_claims") or [])
        }
        if alts:
            new["alternates"] = alts
        else:
            new.pop("alternates", None)
    if ch.note is not None:
        new["audit_note"] = ch.note
    return new


# --- surgical text edits ----------------------------------------------------------------------------------


def _split_blocks(lines: list[str]) -> tuple[list[str], list[tuple[str, list[str], int]]]:
    """(prefix lines, [(item id, block lines, list indent)])."""
    starts = [i for i, ln in enumerate(lines) if _ITEM_RE.match(ln)]
    if not starts:
        return lines, []
    blocks = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        m = _ITEM_RE.match(lines[s])
        assert m is not None
        iid = yaml.safe_load(lines[s].strip()[2:])["id"]
        blocks.append((str(iid), lines[s:e], len(m.group(1))))
    return lines[: starts[0]], blocks


def _extent(block: list[str], key: str, ind: int) -> tuple[int, int] | None:
    k = " " * (ind + 2)
    for i, ln in enumerate(block):
        if ln.startswith(f"{k}{key}:"):
            j = i + 1
            while j < len(block) and (block[j].startswith(k + " ") or block[j].startswith(k + "- ")):
                j += 1
            return i, j
    return None


def _render(key: str, value: Any, ind: int) -> list[str]:
    body = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, width=120)
    pad = " " * (ind + 2)
    return [pad + ln + "\n" for ln in body.splitlines()]


def _set(block: list[str], key: str, value: Any, ind: int, after: str | None = None) -> None:
    new = _render(key, value, ind)
    ext = _extent(block, key, ind)
    if ext is not None:
        block[ext[0] : ext[1]] = new
        return
    anchor = _extent(block, after, ind) if after else None
    at = anchor[1] if anchor else len(block)
    block[at:at] = new


def _remove(block: list[str], key: str, ind: int) -> None:
    ext = _extent(block, key, ind)
    if ext is not None:
        del block[ext[0] : ext[1]]


def patch_text(text: str, changes: Mapping[str, Change], raws: Mapping[str, Mapping[str, Any]]) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    prefix, blocks = _split_blocks(lines)
    out = list(prefix)
    for iid, block, ind in blocks:
        ch = changes.get(iid)
        if ch is not None:
            new = expected_raw(raws[iid], ch)
            if "audit_status" not in raws[iid] or raws[iid]["audit_status"] != ch.status:
                _set(block, "audit_status", ch.status, ind, after="answer_type")
            if ch.claims is not None and list(raws[iid].get("required_claims", [])) != list(ch.claims):
                _set(block, "required_claims", list(ch.claims), ind)
                if "alternates" in raws[iid]:
                    if new.get("alternates"):
                        _set(block, "alternates", new["alternates"], ind)
                    else:
                        _remove(block, "alternates", ind)
            if ch.note is not None and raws[iid].get("audit_note") != ch.note:
                _set(block, "audit_note", ch.note, ind)
        out += block
    return "".join(out)


# --- dataset files ----------------------------------------------------------------------------------------


def dataset_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [
        f
        for f in [
            root / "golden.yaml",
            *sorted((root / "parts").glob("*.yaml")),
            *sorted((root / "authored").glob("*.yaml")),
        ]
        if f.exists()
    ]


def apply(queue: Mapping[str, Any], root: pathlib.Path) -> tuple[dict[pathlib.Path, str], dict[str, Change]]:
    """New file texts (changed files only) and the changes applied. Raises `ApplyError` and writes nothing."""
    texts = {f: f.read_text(encoding="utf-8") for f in dataset_files(root)}
    parsed = {f: (yaml.safe_load(t) or {}).get("items", []) for f, t in texts.items()}
    raws: dict[str, dict[str, Any]] = {r["id"]: r for items in parsed.values() for r in items}
    problems = check_queue(queue, raws)
    if problems:
        raise ApplyError(problems)
    changes = plan_changes(queue, raws)
    new_texts: dict[pathlib.Path, str] = {}
    final_all: list[dict[str, Any]] = []
    for f, t in texts.items():
        mine = {i: c for i, c in changes.items() if any(r["id"] == i for r in parsed[f])}
        new = patch_text(t, mine, raws) if mine else t
        want = [expected_raw(r, mine[r["id"]]) if r["id"] in mine else r for r in parsed[f]]
        got = (yaml.safe_load(new) or {}).get("items", [])
        if got != want:
            raise ApplyError(
                [f"{f.name}: the patched file does not parse to the intended items (not written)"]
            )
        final_all += want
        if new != t:
            new_texts[f] = new
    try:
        validate_items_v2(final_all)
    except DatasetError as e:
        raise ApplyError([f"the resulting dataset is invalid: {p}" for p in e.problems]) from e
    return new_texts, changes


def summary(queue: Mapping[str, Any], changes: Mapping[str, Change], n_files: int) -> str:
    dec = Counter(q["decision"] for q in queue["queue"])
    auto = sum(1 for i in changes if i not in {q["id"] for q in queue["queue"]})
    st = Counter(c.status for c in changes.values())
    return "\n".join(
        [
            "decisions: " + (", ".join(f"{d}={dec.get(d, 0)}" for d in DECISIONS)),
            f"auto-verified (unqueued): {auto}",
            f"after: verified={st['verified']} dropped={st['dropped']}; files changed: {n_files}",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.build.apply_audit")
    ap.add_argument("--queue", type=pathlib.Path, default=QUEUE_FILE)
    ap.add_argument("--dataset-dir", type=pathlib.Path, default=DATASET_V2_DIR)
    ap.add_argument("--check", action="store_true", help="validate and print the summary, write nothing")
    a = ap.parse_args(argv)
    queue = load_queue(a.queue)
    try:
        new_texts, changes = apply(queue, a.dataset_dir)
    except ApplyError as e:
        for p in e.problems:
            print(f"refused: {p}", file=sys.stderr)
        return 2
    if not a.check:
        for f, t in new_texts.items():
            f.write_text(t, encoding="utf-8")
    print(summary(queue, changes, len(new_texts)) + ("  (check only, nothing written)" if a.check else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
