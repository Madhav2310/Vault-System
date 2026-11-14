# ruff: noqa: E501
"""One file for every human audit task: `AUDIT.md`.

    python -m eval.build.audit_workbook render --answers run1.answers.jsonl [run2.answers.jsonl ...] [--pairs 200]
    python -m eval.build.audit_workbook apply  [--check]

`render` writes AUDIT.md (gold-audit decisions + judge-label pairs) and the empty label file
`eval/labels/labeled.yaml`. You fill in the lines that start with `>>>`. `apply` reads AUDIT.md and writes the
decisions into `eval/datasets/vault_eval_v2/audit_queue.yaml` and the labels into `eval/labels/labeled.yaml`;
it never touches the dataset itself (run `python -m eval.build.apply_audit` afterwards). `--check` only reports
what is filled, missing or invalid.

Label pairs come from items outside the gold-audit queue, so their gold claims cannot change under you.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections.abc import Sequence
from typing import Any

import yaml
from eval.judge import (
    EXPECTED_BEHAVIOUR,
    abstain_applicable,
    dump_labels,
    export_labels,
    load_answers,
    load_labels,
)
from eval.loader import load_dataset_v2

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKBOOK = ROOT / "AUDIT.md"
QUEUE = ROOT / "eval/datasets/vault_eval_v2/audit_queue.yaml"
LABELS = ROOT / "eval/labels/labeled.yaml"
DECISIONS = ("gold_wrong", "gold_incomplete", "ambiguous", "system_wrong")
CLIP = 700

HEADER = """# Audit workbook

Everything that needs a human, in one file. Fill in the lines that start with `>>>` and save. Tell Claude when
you are done; it runs `python -m eval.build.audit_workbook apply` and then the dataset apply step.

## How to fill it in

**Part A: gold audit** ({n_full} items to look at, then {n_skim} proposals to skim). The gold is the list of
claims a correct answer must state. For each item decide whose fault the failure is:

| decision | meaning |
|---|---|
| `accept` | take my proposed decision (and proposed fix) as it is |
| `system_wrong` | the gold is right, Vault's answer is wrong; nothing changes |
| `gold_wrong` | a gold claim is wrong or not in the document; write the corrected full claim list under FIX |
| `gold_incomplete` | the gold misses something a correct answer needs; write the corrected full claim list under FIX |
| `ambiguous` | the question has no single right answer; the item is dropped (say why in NOTE) |

Under `>>> FIX:` write the corrected FULL list of required claims, one per line starting with `- `.
Leave `>>> DECISION:` empty on a line you have not reviewed; `apply` reports what is still empty.

**Part B: judge labels** ({n_pairs} answers). Read the answer text only, as a careful reader, and write `y` or `n`
after each `>>>` line: is the claim stated by the answer (paraphrases and equivalent numbers and units count,
extra detail is irrelevant); is each segment entailed by its own cited passages; for the abstention checks, did
the answer decline or correct the premise as required. Leave a label empty to skip it. Do not look at the gold
audit decisions while labelling; label what the text says.

---
"""


def _clip(text: Any, n: int = CLIP) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[:n].rstrip() + " ..."


def _item_block(tag: str, q: dict[str, Any], *, full: bool) -> list[str]:
    out = [f"### {tag} | {q['id']} | {q['stratum']}", f"Q: {_clip(q['question'])}", "GOLD CLAIMS:"]
    out += [f"  c{i}: {_clip(c, 400)}" for i, c in enumerate(q["required_claims"])]
    if full:
        for ev in q.get("gold_evidence") or []:
            out.append(f"GOLD EVIDENCE ({ev.get('doc')} {ev.get('sentences')}): {_clip(ev.get('text'), 500)}")
        out.append(f"VAULT: {_clip(q.get('vault_answer'))}")
        out.append(f"STRONG MODEL: {_clip(q.get('strong_answer'))}")
        out.append(f"QUEUED BECAUSE: {', '.join(q.get('reasons') or [])}")
    pd = q.get("proposed_decision")
    out.append(f"PROPOSED: {pd}. {_clip(q.get('proposed_reason'), 500)}")
    if q.get("proposed_fix"):
        out.append("PROPOSED FIX:")
        out += [f"  - {_clip(c, 400)}" for c in q["proposed_fix"]]
    out += [">>> DECISION: ", ">>> FIX:", ">>> NOTE: ", ""]
    return out


def _pair_block(i: int, p: Any, item: Any) -> list[str]:
    out = [f"### B{i:03d} | {p.pair_id} | {p.stratum} | split={p.split}", f"Q: {_clip(p.question, 900)}"]
    out.append(f"ANSWER: {_clip(p.answer, 2500)}")
    if item.required_claims:
        out.append("REQUIRED CLAIMS (is each one stated by the answer?)")
        for k, c in enumerate(p.required_claims):
            out += [f"  c{k}: {_clip(c, 400)}", f">>> c{k}: "]
    if p.segments:
        out.append("ANSWER SEGMENTS (is each one entailed by its own cited passages?)")
        for s, seg in enumerate(p.segments):
            out.append(f"  s{s}: {_clip(seg.text, 500)}")
            out += [f"      passage: {_clip(x, 350)}" for x in seg.passages] or ["      (no passages cited)"]
            out.append(f">>> s{s}: ")
    if abstain_applicable(item):
        out += [
            f"EXPECTED BEHAVIOUR: {EXPECTED_BEHAVIOUR[item.stratum]}",
            ">>> abstain (did it behave so?): ",
        ]
    out += [">>> notes: ", ""]
    return out


def render(answer_files: Sequence[str], n_pairs: int, seed: int) -> str:
    queue = yaml.safe_load(QUEUE.read_text("utf-8"))["queue"]
    full = [q for q in queue if q.get("proposed_decision") != "system_wrong"]
    skim = [q for q in queue if q.get("proposed_decision") == "system_wrong"]
    ds = load_dataset_v2()
    items = ds.by_id()
    queued_ids = {q["id"] for q in queue}
    answers = [a for f in answer_files for a in load_answers(f) if a.item_id not in queued_ids]
    lf = export_labels(items, answers, n_pairs, seed)
    dump_labels(lf, str(LABELS))
    lines = [HEADER.format(n_full=len(full), n_skim=len(skim), n_pairs=len(lf.pairs))]
    lines += ["## Part A1: gold audit, look at each", ""]
    for i, q in enumerate(full, 1):
        lines += _item_block(f"A{i:02d}", q, full=True)
    lines += [
        "## Part A2: skim list (proposed `system_wrong`: gold kept, the failure is Vault's)",
        "",
        "One switch for the whole list. Set it to `yes` to accept every proposal below; to override one, put a",
        "decision on that item's own `>>> DECISION:` line (an item decision always wins over the switch).",
        "",
        ">>> ACCEPT_ALL_SKIM: ",
        "",
    ]
    for i, q in enumerate(skim, 1):
        lines += _item_block(f"S{i:02d}", q, full=False)
    lines += ["## Part B: judge labels", ""]
    for i, p in enumerate(lf.pairs, 1):
        lines += _pair_block(i, p, items[p.item_id])
    WORKBOOK.write_text("\n".join(lines), "utf-8")
    return f"wrote {WORKBOOK} ({len(full)} + {len(skim)} gold items, {len(lf.pairs)} label pairs)"


# --- apply -------------------------------------------------------------------------------------------------

_BLOCK = re.compile(r"^### (\S+) \| (\S+)", re.M)
_ANSWER = re.compile(r"^>>> (c\d+|s\d+|abstain|notes)[^:\n]*:[ \t]*(.*)$", re.M)


def _blocks(text: str) -> dict[tuple[str, str], str]:
    marks = list(_BLOCK.finditer(text))
    out: dict[tuple[str, str], str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[(m.group(1), m.group(2))] = text[m.start() : end]
    return out


def _field(block: str, name: str) -> str:
    m = re.search(rf"^>>> {name}:[ \t]*(.*)$", block, re.M)
    return m.group(1).strip() if m else ""


def _fix_lines(block: str) -> list[str]:
    m = re.search(r"^>>> FIX:[ \t]*\n((?:[ \t]*- .*\n?)*)", block, re.M)
    if not m:
        return []
    return [ln.strip()[2:].strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ")]


def _yn(value: str) -> bool | None:
    v = value.strip().lower()
    return True if v in ("y", "yes", "true") else False if v in ("n", "no", "false") else None


def apply(check: bool) -> tuple[list[str], dict[str, int]]:
    text = WORKBOOK.read_text("utf-8")
    blocks = _blocks(text)
    problems: list[str] = []
    stat = {"gold_decided": 0, "gold_empty": 0, "labels_set": 0, "labels_empty": 0}
    raw = QUEUE.read_text("utf-8")
    header = "".join(ln + "\n" for ln in raw.splitlines() if ln.startswith("#"))
    queue = yaml.safe_load(raw)
    switch = _yn(_field(text, "ACCEPT_ALL_SKIM")) is True
    for q in queue["queue"]:
        block = next((b for (tag, iid), b in blocks.items() if iid == q["id"] and tag[0] in "AS"), None)
        if block is None:
            problems.append(f"{q['id']}: not in AUDIT.md")
            continue
        d = _field(block, "DECISION").lower()
        skim = q.get("proposed_decision") == "system_wrong" and block.lstrip().startswith("### S")
        if not d and skim and switch:
            d = "accept"
        if not d:
            stat["gold_empty"] += 1
            continue
        fix = _fix_lines(block)
        if d == "accept":
            if not q.get("proposed_decision"):
                problems.append(f"{q['id']}: `accept` but there is no proposal")
                continue
            q["decision"], q["fix"] = q["proposed_decision"], q.get("proposed_fix")
            q["note"] = _field(block, "NOTE") or q.get("proposed_reason")
        elif d in DECISIONS:
            q["decision"], q["fix"], q["note"] = d, fix or None, _field(block, "NOTE") or None
            if d in ("gold_wrong", "gold_incomplete") and not fix:
                problems.append(f"{q['id']}: {d} needs a FIX list")
        else:
            problems.append(f"{q['id']}: unknown decision {d!r}")
            continue
        stat["gold_decided"] += 1
    labels = load_labels(str(LABELS))
    for p in labels.pairs:
        block = next(
            (b for (tag, pid), b in blocks.items() if tag.startswith("B") and pid == p.pair_id), None
        )
        if block is None:
            problems.append(f"pair {p.pair_id}: not in AUDIT.md")
            continue
        vals = {m.group(1): m.group(2) for m in _ANSWER.finditer(block)}
        if p.required_claims:
            p.claim_labels = [_yn(vals.get(f"c{k}", "")) for k in range(len(p.required_claims))]
        if p.segments:
            p.support_labels = [_yn(vals.get(f"s{s}", "")) for s in range(len(p.segments))]
        p.abstain_label = _yn(vals.get("abstain", ""))
        p.notes = vals.get("notes", "").strip()
        got = [*p.claim_labels, *p.support_labels, p.abstain_label]
        stat["labels_set"] += sum(v is not None for v in got)
        stat["labels_empty"] += sum(v is None for v in got) - (
            1 if p.abstain_label is None and not _expects(p) else 0
        )
    if not check and not problems:
        QUEUE.write_text(
            header + yaml.safe_dump(queue, sort_keys=False, allow_unicode=True, width=120), "utf-8"
        )
        dump_labels(labels, str(LABELS))
    return problems, stat


def _expects(p: Any) -> bool:
    return p.stratum in EXPECTED_BEHAVIOUR


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--answers", nargs="+", required=True)
    r.add_argument("--pairs", type=int, default=200)
    r.add_argument("--seed", type=int, default=0)
    a = sub.add_parser("apply")
    a.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "render":
        if WORKBOOK.exists() and "--force" not in (argv or sys.argv):
            print(f"refusing to overwrite {WORKBOOK} (it may hold your answers); move it away first")
            return 2
        print(render(args.answers, args.pairs, args.seed))
        return 0
    problems, stat = apply(args.check)
    print(stat)
    for pr in problems:
        print("PROBLEM:", pr)
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
