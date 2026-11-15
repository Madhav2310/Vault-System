"""Cross-model gold audit (the "Platinum" method), tool only.

For every live item two answers are collected: Vault's (read from an answers file of an earlier run, never
re-run here) and a strong model's (`flagship` through `app.llm.gateway`, prompt `eval_audit_gold@1`), the
latter given the question and the GOLD evidence text only. Both are graded against the gold by the existing
judges (`eval.judge.judge_claims` against `required_claims`; `judge_abstention` for abstention strata).
An item is queued for a human when

  - either answer fails the gold (a required claim not entailed, or the abstention judge says incorrect), or
  - the two answers disagree on a required claim (one entails it, the other does not).

Everything is decided by code; the model only writes the strong answer and the judges' verdicts. Unqueued
audited items become `verified` automatically when `eval.build.apply_audit` runs; queued ones only after a
human fills `decision` (gold_wrong | gold_incomplete | ambiguous | system_wrong) in `audit_queue.yaml`.

Vault answers file (`--answers`), one of
  - JSONL, one object per line: {"item_id": ..., "answer": ...}  (the `eval.judge.load_answers` format), or
  - JSON: {"items": [{"item_id" | "id": ..., "answer": ...}]} or {"answers": {"<id>": "<text>"}}.
(The sidecar report carries no answer text; the runner writes the answers file beside it.)

CLI (from backend/, PYTHONPATH=..):
    python -m eval.build.audit --answers run_answers.jsonl --max-usd 2           # live, cap mandatory
    python -m eval.build.audit --answers run_answers.jsonl --dry-run --limit 5   # scripted provider, $0
    python -m eval.build.audit --render ../eval/datasets/vault_eval/audit_queue.yaml       # review markdown
Prints counts and the spend from the gateway ledger against the cap; exit 3 when the cap stopped the audit
(the queue then lists only what was audited, and `apply_audit` leaves the rest alone).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import yaml
from eval.judge import (
    JudgeOutputError,
    SpendCapReached,
    SpendGuard,
    abstain_applicable,
    check_tiers,
    gather_bounded,
    judge_abstention,
    judge_claims,
)
from eval.loader import DATASET_DIR, load_dataset
from eval.schema import GoldItem
from eval.splits import live_items
from pydantic import BaseModel, ConfigDict

from app.llm.gateway import LlmGateway, MemoryLedger, render_inputs
from app.llm.routing import Route

PURPOSE = "eval_audit"
PROMPT = ("eval_audit_gold", 1)
QUEUE_FILE = DATASET_DIR / "audit_queue.yaml"
DECISIONS = ("gold_wrong", "gold_incomplete", "ambiguous", "system_wrong")
UNITS_UNAVAILABLE = "gold evidence text unavailable (document sentences could not be loaded)"
NO_ANSWER_TEXT = "The documents do not contain this information."

UnitsOf = Callable[[str], Sequence[str] | None]


class AuditAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str


# --- inputs -----------------------------------------------------------------------------------------------


def load_vault_answers(path: str | pathlib.Path) -> dict[str, str]:
    """item id -> Vault's answer text, from JSONL or JSON (see the module docstring)."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    rows: list[Any]
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        doc = None
    if isinstance(doc, dict) and isinstance(doc.get("answers"), dict):
        return {str(k): str(v) for k, v in doc["answers"].items()}
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        rows = doc["items"]
    elif isinstance(doc, list):
        rows = doc
    else:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    out: dict[str, str] = {}
    for r in rows:
        iid = r.get("item_id", r.get("id")) if isinstance(r, dict) else None
        if iid is None or not isinstance(r.get("answer"), str):
            continue  # a metrics-only row (a sidecar) carries no answer text
        out[str(iid)] = r["answer"]
    if not out:
        raise SystemExit(f"{path}: no (item id, answer) rows found (a sidecar has metrics only)")
    return out


def gold_passages(item: GoldItem, units_of: UnitsOf) -> list[dict[str, Any]] | None:
    """One passage per gold range (its sentences joined); None when any range cannot be turned into text."""
    out: list[dict[str, Any]] = []
    for g in item.gold_evidence:
        units = units_of(g.doc)
        if units is None or g.sentence_end > len(units):
            return None
        out.append(
            {
                "doc": g.doc,
                "sentences": f"{g.sentence_start}-{g.sentence_end}",
                "text": " ".join(units[g.sentence_start : g.sentence_end]),
            }
        )
    return out


# --- the strong answer ------------------------------------------------------------------------------------


def strong_inputs(item: GoldItem, passages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "question": item.question,
        "earlier_user_turns": list(item.prior_turns),
        "passages": [{"doc": p["doc"], "text": p["text"]} for p in passages],
    }


def _worst_case(gw: LlmGateway, inputs: Mapping[str, Any]) -> Decimal:
    p = gw.prompts.get(*PROMPT)
    res = gw.router.resolve(PURPOSE, None, default=Route(p.role, p.effort))
    price = gw.prices.price(res.model)
    in_tok = (len(p.body) + len(render_inputs(inputs))) // 4 + 1
    return (Decimal(in_tok) * price.input + Decimal(p.max_tokens) * price.output) / Decimal(1_000_000)


async def strong_answer(
    gw: LlmGateway, item: GoldItem, passages: Sequence[Mapping[str, Any]], guard: SpendGuard
) -> str:
    inputs = strong_inputs(item, passages)
    guard.check(_worst_case(gw, inputs))
    res = await gw.call(
        purpose=PURPOSE,
        lane="background",
        prompt=PROMPT,
        owner_id=None,
        schema=AuditAnswer,
        inputs=inputs,
    )
    if res.output is None:
        raise JudgeOutputError("no parsed output")
    return str(res.output.answer)


# --- grading and the queue rule (pure) --------------------------------------------------------------------


@dataclass
class Side:
    """One answer graded against the gold. `claims`: per required claim entailed?; `abstain_ok`: abstention
    judge verdict (None when not applicable)."""

    answer: str | None
    claims: tuple[bool, ...] | None = None
    abstain_ok: bool | None = None

    @property
    def graded(self) -> bool:
        return self.claims is not None or self.abstain_ok is not None

    @property
    def failed_claims(self) -> list[int]:
        return [i for i, e in enumerate(self.claims or ()) if not e]

    @property
    def fails(self) -> bool:
        return bool(self.failed_claims) or self.abstain_ok is False


@dataclass
class Audited:
    item: GoldItem
    passages: list[dict[str, Any]]
    vault: Side
    strong: Side
    errors: list[str] = field(default_factory=list)

    @property
    def disagree_claims(self) -> list[int]:
        if self.vault.claims is None or self.strong.claims is None:
            return []
        return [
            i for i, (a, b) in enumerate(zip(self.vault.claims, self.strong.claims, strict=True)) if a != b
        ]

    @property
    def abstain_disagree(self) -> bool:
        a, b = self.vault.abstain_ok, self.strong.abstain_ok
        return a is not None and b is not None and a != b

    @property
    def reasons(self) -> list[str]:
        r: list[str] = []
        if self.vault.fails:
            r.append("vault_fails_gold")
        if self.strong.fails:
            r.append("strong_fails_gold")
        if self.disagree_claims or self.abstain_disagree:
            r.append("answers_disagree")
        if self.errors:
            r.append("judge_error")
        return r

    @property
    def queued(self) -> bool:
        return bool(self.reasons)


def queue_entry(a: Audited) -> dict[str, Any]:
    it = a.item
    return {
        "id": it.id,
        "stratum": it.stratum,
        "source_doc": it.source_doc,
        "question": it.question,
        "answer_type": it.answer_type,
        "required_claims": list(it.required_claims),
        "gold_evidence": [
            {"doc": p["doc"], "sentences": p["sentences"], "text": p["text"]} for p in a.passages
        ],
        "vault_answer": a.vault.answer,
        "strong_answer": a.strong.answer,
        "reasons": a.reasons,
        "failed": {
            "vault_claims": a.vault.failed_claims,
            "strong_claims": a.strong.failed_claims,
            "disagree_claims": a.disagree_claims,
            "vault_abstain_wrong": a.vault.abstain_ok is False,
            "strong_abstain_wrong": a.strong.abstain_ok is False,
        },
        "errors": list(a.errors),
        "decision": None,  # gold_wrong | gold_incomplete | ambiguous | system_wrong
        "fix": None,  # for gold_wrong / gold_incomplete: the corrected FULL list of required claims
        "note": None,  # for ambiguous: why; optional otherwise
    }


# --- the run ----------------------------------------------------------------------------------------------


@dataclass
class AuditResult:
    audited: list[Audited] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # item id -> reason (stays draft)
    stopped: bool = False  # the spend cap stopped the run

    @property
    def queue(self) -> list[Audited]:
        return [a for a in self.audited if a.queued]


async def _grade(
    gw: LlmGateway, item: GoldItem, answer: str | None, guard: SpendGuard, errors: list[str], who: str
) -> Side:
    side = Side(answer)
    if answer is None:
        return side
    try:
        if item.required_claims:
            side.claims = (await judge_claims(gw, item, answer, guard=guard)).entailed
        if abstain_applicable(item):
            side.abstain_ok = (await judge_abstention(gw, item, answer, guard=guard)).correct
    except JudgeOutputError as e:
        errors.append(f"{who}: {e}")
    return side


async def audit_one(
    gw: LlmGateway,
    item: GoldItem,
    vault_answer: str | None,
    units_of: UnitsOf,
    guard: SpendGuard,
) -> Audited | str:
    """Audited record, or a skip reason when the item cannot be audited (no gold text)."""
    passages = gold_passages(item, units_of)
    if passages is None:
        return UNITS_UNAVAILABLE
    errors: list[str] = []
    try:
        strong_text: str | None = await strong_answer(gw, item, passages, guard)
    except JudgeOutputError as e:
        strong_text = None
        errors.append(f"strong answer: {e}")
    vault = await _grade(gw, item, vault_answer, guard, errors, "vault grading")
    strong = await _grade(gw, item, strong_text, guard, errors, "strong grading")
    return Audited(item, passages, vault, strong, errors)


async def run_audit(
    gw: LlmGateway,
    items: Sequence[GoldItem],
    vault_answers: Mapping[str, str],
    units_of: UnitsOf,
    guard: SpendGuard,
    *,
    concurrency: int = 4,
) -> AuditResult:
    check_tiers(gw.router)
    res = AuditResult()

    async def one(item: GoldItem) -> tuple[GoldItem, Audited | str | None]:
        try:
            return item, await audit_one(gw, item, vault_answers.get(item.id), units_of, guard)
        except SpendCapReached:
            return item, None

    results: list[tuple[GoldItem, Audited | str | None]] = await gather_bounded(
        [one(i) for i in live_items(items)], concurrency
    )
    for item, out in results:
        if out is None:
            res.stopped = True
            res.skipped[item.id] = "spend cap reached before this item was audited"
        elif isinstance(out, str):
            res.skipped[item.id] = out
        else:
            res.audited.append(out)
    return res


# --- queue file -------------------------------------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data and data == data.strip() else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _str)

HEADER = (
    "# Gold audit queue (generated by eval/build/audit.py; decisions are yours).\n"
    "# For every queued item set `decision`: gold_wrong | gold_incomplete | ambiguous | system_wrong.\n"
    "#   gold_wrong / gold_incomplete: also set `fix` to the corrected FULL list of required claims.\n"
    "#   ambiguous: the item is dropped (say why in `note`).  system_wrong: gold right, system not.\n"
    "# Then: python -m eval.build.apply_audit   (refuses while any queued item has no decision)\n"
)


def queue_document(res: AuditResult, *, spent: Decimal, cap: Decimal | None) -> dict[str, Any]:
    reasons = Counter(r for a in res.queue for r in a.reasons)
    return {
        "meta": {
            "audited": len(res.audited),
            "queued": len(res.queue),
            "skipped": len(res.skipped),
            "reasons": dict(sorted(reasons.items())),
            "vault_answers_missing": sum(a.vault.answer is None for a in res.audited),
            "spent_usd": str(spent),
            "cap_usd": None if cap is None else str(cap),
            "stopped_on_budget": res.stopped,
        },
        "audited": sorted(a.item.id for a in res.audited),
        "skipped": dict(sorted(res.skipped.items())),
        "queue": [queue_entry(a) for a in res.queue],
    }


def dump_queue(doc: Mapping[str, Any]) -> str:
    return HEADER + yaml.dump(dict(doc), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


def load_queue(path: str | pathlib.Path = QUEUE_FILE) -> dict[str, Any]:
    doc = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}
    doc.setdefault("queue", [])
    doc.setdefault("audited", [])
    return dict(doc)


def has_decisions(path: pathlib.Path) -> bool:
    if not path.exists():
        return False
    return any(q.get("decision") for q in load_queue(path)["queue"] or [])


# --- human review rendering -------------------------------------------------------------------------------


def _clip(s: str | None, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


_RANK = {None: 0, "gold_wrong": 1, "gold_incomplete": 2, "ambiguous": 3, "system_wrong": 4}


def render_review(queue_doc: Mapping[str, Any], *, width: int = 200) -> str:
    """Markdown, four lines per queued item (question, gold claims, the two answers, the proposed decision).
    Items whose gold is suspect come first, `system_wrong` proposals last."""
    m = queue_doc.get("meta", {})
    lines = [
        "# Gold audit review",
        "",
        f"{len(queue_doc['queue'])} queued of {m.get('audited', '?')} audited. "
        "Confirm or change each: decision = "
        "gold_wrong | gold_incomplete | ambiguous | system_wrong (edit `audit_queue.yaml`), or accept every "
        "proposal with `apply_audit --accept-proposed`.",
        "",
    ]
    ordered = sorted(queue_doc["queue"], key=lambda q: _RANK.get(q.get("proposed_decision"), 0))
    skim = [
        q
        for q in ordered
        if q.get("proposed_decision") == "system_wrong" and q.get("rca_gold_audit") != "CONFIDENT"
    ]
    look = [q for q in ordered if q not in skim]
    if skim:
        lines[2] += f" {len(look)} need a look; {len(skim)} are proposed system_wrong (skim list at the end)."
    for n, q in enumerate(look, 1):
        f = q["failed"]
        who = []
        if f["vault_claims"] or f["vault_abstain_wrong"]:
            who.append("vault fails " + (",".join(f"c{i}" for i in f["vault_claims"]) or "abstention"))
        if f["strong_claims"] or f["strong_abstain_wrong"]:
            who.append("strong fails " + (",".join(f"c{i}" for i in f["strong_claims"]) or "abstention"))
        if q.get("errors"):
            who.append("judge error")
        claims = " | ".join(f"c{i} {c}" for i, c in enumerate(q["required_claims"])) or "(abstain: no claims)"
        lines += [
            f"**{n}. `{q['id']}`** ({q['stratum']}; {'; '.join(who) or 'queued'}) "
            f"Q: {_clip(q['question'], width)}  ",
            f"GOLD: {_clip(claims, width)}  ",
            f"VAULT: {_clip(q['vault_answer'] or '(no answer)', width // 2)} ‖ "
            f"STRONG: {_clip(q['strong_answer'] or '(no answer)', width // 2)}  ",
        ]
        if "proposed_decision" in q:
            fix = f" fix: {_clip(' | '.join(q['proposed_fix']), width)}" if q.get("proposed_fix") else ""
            why = _clip(q.get("proposed_reason"), width)
            lines.append(f"PROPOSED: {q['proposed_decision'] or 'none'} — {why}{fix}")
        lines.append("")
    if skim:
        lines += [
            "## Proposed system_wrong (no claim fails for both answers; skim, flip any you doubt)",
            "",
        ]
        lines += [f"- `{q['id']}` ({q['stratum']}): {_clip(q.get('proposed_reason'), width)}" for q in skim]
        lines.append("")
    return "\n".join(lines)


# --- gateways ---------------------------------------------------------------------------------------------


def _dry_gateway(ledger: MemoryLedger) -> LlmGateway:
    """Scripted provider: the strong answer echoes the evidence passages (or declines when there are none);
    judges entail a claim iff its text occurs in the answer. Valid for every schema the audit uses."""
    from app.core.settings import Settings
    from app.llm.pricing import DEFAULT_PRICES, PriceTable
    from app.llm.prompts import default_registry
    from app.llm.providers.fake import FakeProvider
    from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage

    class DryProvider(FakeProvider):
        async def complete(self, req: ChatRequest) -> ChatResponse:
            self.requests.append(req)
            data = json.loads(
                str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
            )
            props = (req.output_schema or {}).get("properties", {})
            body: dict[str, Any]
            if "answer" in props:
                texts = [p["text"] for p in data.get("passages", [])]
                body = {"answer": " ".join(texts) or NO_ANSWER_TEXT}
            elif "decisions" in props:
                ans = str(data["answer"])
                decisions = []
                for c in data["claims"]:
                    hit = c["claim"].casefold() in ans.casefold()
                    decisions.append(
                        {"claim_index": c["index"], "entailed": hit, "quote": ans[:20] if hit else ""}
                    )
                body = {"decisions": decisions}
            else:  # abstention verdict
                declined = "do not contain" in str(data["answer"]).casefold()
                body = {
                    "reason": "dry run",
                    "behaviour": "declined" if declined else "answered_as_asked",
                    "correct": declined,
                }
            return ChatResponse((TextPart(json.dumps(body)),), "end_turn", Usage(1500, 150), None, req.model)

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    return LlmGateway(
        chat=DryProvider(),
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings,
    )


def default_units_of() -> UnitsOf:
    """Document sentence streams from `documents.yaml` (PDFs are parsed once and cached by the sampler)."""
    from eval.build.build_docs import document_units, load_documents

    docs = load_documents()
    cache: dict[str, Sequence[str] | None] = {}

    def units(key: str) -> Sequence[str] | None:
        if key not in cache:
            entry = docs.get(key)
            cache[key] = None if entry is None else document_units(key, entry)
        return cache[key]

    return units


# --- cli --------------------------------------------------------------------------------------------------


def summary(res: AuditResult, spent: Decimal, cap: Decimal | None) -> str:
    cap_s = f" of cap ${cap}" if cap is not None else ""
    by = Counter(r for a in res.queue for r in a.reasons)
    return "\n".join(
        [
            f"audited {len(res.audited)}, queued {len(res.queue)}, skipped {len(res.skipped)}"
            + (" (stopped: spend cap)" if res.stopped else ""),
            "queue reasons: " + (", ".join(f"{k}={v}" for k, v in sorted(by.items())) or "none"),
            f"spent ${spent}{cap_s}",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.build.audit")
    ap.add_argument("--answers", help="Vault answers from an earlier run (JSONL or JSON; see the docstring)")
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument(
        "--dry-run", action="store_true", help="scripted FakeProvider, no network, writes nothing"
    )
    ap.add_argument("--out", type=pathlib.Path, default=None, help=f"queue file (default {QUEUE_FILE.name})")
    ap.add_argument("--force", action="store_true", help="overwrite a queue file that already has decisions")
    ap.add_argument("--limit", type=int, default=None, help="audit only the first N live items")
    ap.add_argument(
        "--split", default="all", choices=["all", "train", "dev", "test"], help="audit one split only"
    )
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--render", type=pathlib.Path, metavar="QUEUE_YAML", help="print the review markdown")
    ap.add_argument("--render-out", type=pathlib.Path, default=None, help="write the review markdown here")
    a = ap.parse_args(argv)
    if a.render:
        md = render_review(load_queue(a.render))
        if a.render_out:
            a.render_out.write_text(md + "\n", encoding="utf-8")
            print(f"wrote {a.render_out}")
        else:
            print(md)
        return 0
    if not a.answers:
        ap.error("--answers is required (or --render)")
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    out = a.out or (None if a.dry_run else QUEUE_FILE)
    if out is not None and not a.force and has_decisions(out):
        print(f"{out} already holds human decisions; pass --force to overwrite", file=sys.stderr)
        return 2
    ledger = MemoryLedger()
    if a.dry_run:
        gw = _dry_gateway(ledger)
    else:
        from eval.judge import live_gateway

        gw = live_gateway(ledger)
    cap = a.max_usd
    guard = SpendGuard(ledger, cap if cap is not None else Decimal(1000))
    items = list(live_items(load_dataset().items))
    if a.split != "all":
        from eval.splits import select_split

        items = select_split(items, a.split)
    if a.limit is not None:
        items = items[: a.limit]
    res = asyncio.run(
        run_audit(
            gw, items, load_vault_answers(a.answers), default_units_of(), guard, concurrency=a.concurrency
        )
    )
    if out is not None:
        out.write_text(dump_queue(queue_document(res, spent=guard.spent, cap=cap)), encoding="utf-8")
        print(f"wrote {out}")
    print(summary(res, guard.spent, cap))
    return 3 if res.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
