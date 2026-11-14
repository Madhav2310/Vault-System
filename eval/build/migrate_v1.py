"""Migrate the 122 v1 golden items to gold schema v2 (plan S21.02).

    python -m eval.build.migrate_v1 --max-usd 0.5   # live: cap mandatory (from backend/, PYTHONPATH=..)
    python -m eval.build.migrate_v1 --dry-run            # scripted FakeProvider, no network, writes nothing

Outputs (live runs): `eval/datasets/vault_eval_v2/parts/migrated.yaml` (every v1 item, converted or dropped)
and `migration_review.md` (original answer next to the drafted claims, plus the drop list).

Rules
- **Stratum** by category (`stratum_of`): lookup -> conditional / multi_span / single_span by question cues (a
  FinanceBench item whose label says numerical reasoning -> numeric_calc), table -> table_lookup, compare ->
  compare_docs, conflict -> conflict, unanswerable -> unanswerable (answer_type abstain), meta -> aggregation,
  multi_turn -> multi_turn (the last turn is the question, earlier turns are `prior_turns`), identifier ->
  single_span, summarize -> multi_span, injection -> injection.
- **source_doc** = the first scope document, else the first gold document, else `vault` (vault-wide
  meta items).
- **Cap**: at most 3 non-dropped items per `source_doc`, counted together with every v2 item already on disk
  (`golden.yaml`, `parts/*.yaml` except this module's own output, `authored/*.yaml`). Within a document the
  items kept are chosen greedily for distinct question types (new stratum first, then rarer stratum in v1,
  then a new v1 sub-type such as the CUAD category, then id). The rest are `audit_status: dropped`
  with the reason.
- **Claims**: kept answerable items get `required_claims` / `optional_claims` / `alternates` drafted by role
  `balanced` (prompt `eval_migrate_claims@1`, via `app.llm.gateway` only), several items per call, calls run
  concurrently under a spend guard. Dropped items carry the raw v1 reference answer as their claims (no model
  call) because the schema requires claims on answerable items; they do not count toward the cap.
- **Gold evidence**: each v1 gold quote is located with `eval.mapping.map_quote` (exact normalized match,
  fuzzy fallback, on the labelled page) against the document's pages as extracted by the production extractor
  (PDF: `PdfExtractor`, one `ChunkInfo` per page; Markdown/DOCX: one `ChunkInfo` of the fixture sentences) and
  split with the ingestion sentence splitter. The hit's sentence range inside the page is shifted by the
  number of sentences on earlier pages, giving a document-level half-open range. This is a
  chunker-independent approximation: re-map against ingested chunks before relying on it (UNVERIFIED).
- Every item is written with `audit_status: draft` (or `dropped`) and an `audit_note`; claim quality is
  unaudited until S21.05.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import yaml
from eval.judge import SpendCapReached, SpendGuard, gather_bounded
from eval.loader import DATASET_V2_DIR, MAX_ITEMS_PER_DOC, ROOT, load_dataset, validate_items_v2
from eval.mapping import ChunkInfo, map_quote
from eval.schema import MAX_REQUIRED_CLAIMS, GoldenItem
from pydantic import BaseModel, ConfigDict

from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.routing import Route

PURPOSE = "eval_migrate"  # no routing-table row: the prompt's front-matter role (balanced) applies
PROMPT = ("eval_migrate_claims", 1)
PARTS_FILE = "parts/migrated.yaml"
REVIEW_FILE = "migration_review.md"
BATCH_SIZE = 5
REFERENCE_QUOTES = 4
QUOTE_CHARS = 600
VAULT_DOC = "vault"  # source_doc of vault-wide items (no document scope, no gold)


class MigrationError(RuntimeError):
    pass


# --- draft schema (strict-mode compatible: all fields required) -------------------------------------------


class AlternateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    variants: list[str]


class DraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    required_claims: list[str]
    optional_claims: list[str]
    alternates: list[AlternateEntry]


class DraftBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DraftItem]


# --- stratum, source_doc, reference -----------------------------------------------------------------------

_CONDITIONAL = re.compile(
    r"\b(unless|except|provided that|under what (?:conditions?|circumstances)"
    r"|in what (?:cases?|circumstances|events?)|what happens if|notice period|how much notice"
    r"|days'? notice)\b|\bif\b"
)
_MULTI_SPAN = re.compile(r"\b(list|all|which of|who are|what are|each of|every)\b")
_NUMERICAL = re.compile(r"numerical reasoning", re.I)
_CATEGORY_STRATUM = {
    "table": "table_lookup",
    "compare": "compare_docs",
    "conflict": "conflict",
    "unanswerable": "unanswerable",
    "meta": "aggregation",
    "multi_turn": "multi_turn",
    "identifier": "single_span",
    "summarize": "multi_span",
    "injection": "injection",
}


def final_question(item: GoldenItem) -> str:
    return item.turns[-1].question if item.turns else str(item.question)


def prior_turns(item: GoldenItem) -> list[str]:
    return [t.question for t in (item.turns or [])[:-1]]


def stratum_of(item: GoldenItem) -> tuple[str, str]:
    """(stratum, rule) by category; lookups and FinanceBench tables are refined by question cues."""
    if item.category == "table" and _NUMERICAL.search(item.fb_reasoning or ""):
        return "numeric_calc", "table + FinanceBench numerical-reasoning label"
    if item.category in _CATEGORY_STRATUM:
        return _CATEGORY_STRATUM[item.category], f"category {item.category}"
    q = final_question(item).casefold()
    if _NUMERICAL.search(item.fb_reasoning or ""):
        return "numeric_calc", "lookup + FinanceBench numerical-reasoning label"
    if _CONDITIONAL.search(q):
        return "conditional", "lookup + conditional cue in the question"
    if _MULTI_SPAN.search(q):
        return "multi_span", "lookup + list cue in the question"
    return "single_span", "lookup, no conditional or list cue"


def source_doc_of(item: GoldenItem) -> str:
    if item.doc_ids:
        return item.doc_ids[0]
    golds = [item.gold_doc(g) for g in item.gold] + [
        item.gold_doc(g) for t in item.turns or [] for g in t.gold
    ]
    return golds[0] if golds else VAULT_DOC


def _last_golds(item: GoldenItem) -> list[Any]:
    return list(item.turns[-1].gold) if item.turns else list(item.gold)


def reference_of(item: GoldenItem) -> str | list[str]:
    """The original reference answer as the drafting model sees it (rubric lines for summaries)."""
    if item.category == "summarize" and item.rubric:
        return list(item.rubric)
    ans = item.turns[-1].expected_answer if item.turns else item.expected_answer
    assert ans is not None
    return ans if isinstance(ans, str) else [str(a) for a in ans]


def raw_claims(ref: str | list[str]) -> list[str]:
    """Mechanical fallback claims for dropped items: the v1 reference verbatim (one claim per list member)."""
    members = [ref] if isinstance(ref, str) else list(ref)
    return members if len(members) <= MAX_REQUIRED_CLAIMS else ["; ".join(members)]


# --- selection under the per-document cap -----------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    item: GoldenItem
    stratum: str
    rule: str
    source_doc: str
    keep: bool = True
    drop_reason: str | None = None


def _subtype(item: GoldenItem) -> str:
    return item.cuad_category or item.fb_question_type or item.category


def plan_items(items: Sequence[GoldenItem], existing: Mapping[str, int]) -> list[Plan]:
    """Stratum + source_doc per item, then keep up to the cap per document (distinct question types first)."""
    base = []
    for i in items:
        st, rule = stratum_of(i)
        base.append(Plan(i, st, rule, source_doc_of(i)))
    freq = Counter(p.stratum for p in base)
    by_doc: dict[str, list[Plan]] = defaultdict(list)
    for p in base:
        by_doc[p.source_doc].append(p)
    verdict: dict[str, tuple[bool, str | None]] = {}
    for doc, plans in by_doc.items():
        room = max(0, MAX_ITEMS_PER_DOC - existing.get(doc, 0))
        pool = list(plans)
        chosen: list[Plan] = []
        while pool and len(chosen) < room:
            seen_st = {c.stratum for c in chosen}
            seen_sub = {_subtype(c.item) for c in chosen}
            best = min(
                pool,
                key=lambda p: (
                    p.stratum in seen_st,
                    freq[p.stratum],
                    _subtype(p.item) in seen_sub,
                    p.item.id,
                ),
            )
            pool.remove(best)
            chosen.append(best)
        kept_desc = ", ".join(f"{c.item.id} ({c.stratum})" for c in chosen) or "none"
        for p in chosen:
            verdict[p.item.id] = (True, None)
        for p in pool:
            verdict[p.item.id] = (
                False,
                f"over the {MAX_ITEMS_PER_DOC}-per-document cap for source_doc {doc} "
                f"({existing.get(doc, 0)} already on disk); kept {kept_desc}; this {p.stratum} item "
                "ranked lower for question-type diversity",
            )
    return [
        Plan(p.item, p.stratum, p.rule, p.source_doc, verdict[p.item.id][0], verdict[p.item.id][1])
        for p in base
    ]


def existing_counts(root: pathlib.Path) -> Counter[str]:
    """Non-dropped items per source_doc already on disk, excluding this module's own output."""
    files = [
        root / "golden.yaml",
        *sorted((root / "parts").glob("*.yaml")),
        *sorted((root / "authored").glob("*.yaml")),
    ]
    out: Counter[str] = Counter()
    for f in files:
        if not f.exists() or f == root / PARTS_FILE:
            continue
        for r in (yaml.safe_load(f.read_text()) or {}).get("items", []):
            if r.get("audit_status", "draft") != "dropped":
                out[r["source_doc"]] += 1
    return out


def existing_raw(root: pathlib.Path) -> list[dict[str, Any]]:
    files = [
        root / "golden.yaml",
        *sorted((root / "parts").glob("*.yaml")),
        *sorted((root / "authored").glob("*.yaml")),
    ]
    raw: list[dict[str, Any]] = []
    for f in files:
        if f.exists() and f != root / PARTS_FILE:
            raw += (yaml.safe_load(f.read_text()) or {}).get("items", [])
    return raw


# --- gold evidence: quotes -> document-level sentence ranges ----------------------------------------------


@dataclass(frozen=True)
class DocText:
    chunks: tuple[ChunkInfo, ...]
    first_sentence: Mapping[str, int]  # chunk id -> document-level index of the chunk's sentence 0


DocLoader = Callable[[str], DocText | None]


def _locate_source(meta: Mapping[str, Any]) -> pathlib.Path | None:
    rel = pathlib.Path(meta["file"])
    for base in (ROOT, *ROOT.parents[:4]):
        if (base / rel).exists():
            return base / rel
    return None


def default_doc_loader() -> DocLoader:
    """Loads and caches document text for the v1 documents (PDF via the production extractor)."""
    docs = yaml.safe_load((ROOT / "eval" / "datasets" / "vault_eval_v1" / "documents.yaml").read_text())[
        "documents"
    ]
    cache: dict[str, DocText | None] = {}

    def load(key: str) -> DocText | None:
        if key in cache:
            return cache[key]
        from eval.build.check_coverage import fixture_sentences

        from app.ingestion.sentences import split_sentences

        path = _locate_source(docs[key]) if key in docs else None
        out: DocText | None = None
        if path is not None and path.suffix.lower() == ".pdf":
            from app.ingestion.extract.pdf import PdfExtractor

            chunks: list[ChunkInfo] = []
            first: dict[str, int] = {}
            n = 0
            for p in PdfExtractor().extract(str(path)).pages:
                page_spans = tuple(split_sentences(p.text))
                cid = f"{key}:p{p.page_no}"
                chunks.append(
                    ChunkInfo(cid, key, len(chunks), p.text, page_spans, p.page_no or 0, p.page_no or 0)
                )
                first[cid] = n
                n += len(page_spans)
            out = DocText(tuple(chunks), first)
        elif path is not None:
            sents = fixture_sentences(path)
            text = ""
            spans: list[tuple[int, int]] = []
            for s in sents:
                text += ("\n" if text else "") + s
                spans.append((len(text) - len(s), len(text)))
            cid = f"{key}:doc"
            out = DocText((ChunkInfo(cid, key, 0, text, tuple(spans), 0, 0),), {cid: 0})
        cache[key] = out
        return out

    return load


@dataclass
class Evidence:
    ranges: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    unmapped: int = 0
    fuzzy: int = 0
    ambiguous: int = 0


def map_evidence(item: GoldenItem, loader: DocLoader | None) -> Evidence:
    ev = Evidence()
    golds = _last_golds(item)
    ev.total = len(golds)
    seen: set[tuple[str, int, int]] = set()
    for g in golds:
        doc = item.gold_doc(g)
        dt = loader(doc) if loader else None
        if g.match == "not_found" or dt is None:
            ev.unmapped += 1
            continue
        t = map_quote(g.quote, g.page, dt.chunks, item_id=item.id, doc_id=doc)
        if not t.mapped:
            ev.unmapped += 1
            continue
        h = t.hits[0]
        ev.fuzzy += h.match == "fuzzy"
        ev.ambiguous += len(t.hits) > 1
        base = dt.first_sentence[h.chunk_id]
        key = (doc, base + h.sentence_start, base + h.sentence_end)
        if key not in seen:
            seen.add(key)
            ev.ranges.append({"doc": key[0], "sentence_start": key[1], "sentence_end": key[2]})
    return ev


# --- claim drafting ---------------------------------------------------------------------------------------


def draft_inputs(p: Plan) -> dict[str, Any]:
    it = p.item
    quotes = [g.quote[:QUOTE_CHARS] for g in _last_golds(it)][:REFERENCE_QUOTES]
    return {
        "id": it.id,
        "question": final_question(it),
        "prior_turns": prior_turns(it),
        "reference": reference_of(it),
        "evidence_quotes": quotes,
    }


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _digits(s: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUM.finditer(s)}


@dataclass(frozen=True)
class Drafted:
    required: list[str]
    optional: list[str]
    alternates: dict[str, list[str]]
    notes: list[str]


def check_draft(p: Plan, d: DraftItem) -> Drafted:
    """Validate one drafted item; raises ValueError (the batch is retried) on an unusable draft."""
    req = [c.strip() for c in d.required_claims if c.strip()]
    opt = [c.strip() for c in d.optional_claims if c.strip()]
    notes: list[str] = []
    if not req:
        raise ValueError(f"{d.id}: no required claims")
    if len(req) > MAX_REQUIRED_CLAIMS:
        opt = req[MAX_REQUIRED_CLAIMS:] + opt
        req = req[:MAX_REQUIRED_CLAIMS]
        notes.append(f"claims beyond {MAX_REQUIRED_CLAIMS} moved to optional")
    known = {c: c for c in [*req, *opt]}
    alts: dict[str, list[str]] = {}
    for a in d.alternates:
        key = known.get(a.claim.strip())
        variants = [v.strip() for v in a.variants if v.strip()]
        if key is None or not variants:
            notes.append("an alternates entry did not match a claim and was dropped")
            continue
        alts.setdefault(key, [])
        alts[key] += [v for v in variants if v not in alts[key]]
    ref = reference_of(p.item)
    ref_text = ref if isinstance(ref, str) else " ".join(ref)
    have = _digits(" ".join([*req, *opt, *(v for vs in alts.values() for v in vs)]))
    missing = sorted(_digits(ref_text) - have)
    if missing:
        notes.append("number-check: reference numbers not in claims: " + ", ".join(missing[:8]))
    return Drafted(req, opt, alts, notes)


class _Budget:
    """Reserves each call's worst-case cost up front so concurrent calls cannot jointly pass the cap."""

    def __init__(self, guard: SpendGuard) -> None:
        self.guard = guard
        self.reserved = Decimal(0)

    def reserve(self, worst: Decimal) -> None:
        self.guard.check(self.reserved + worst)
        self.reserved += worst

    def release(self, worst: Decimal) -> None:
        self.reserved -= worst


def _worst_case(gw: LlmGateway, inputs: Mapping[str, Any]) -> Decimal:
    from app.llm.gateway import render_inputs

    p = gw.prompts.get(*PROMPT)
    res = gw.router.resolve(PURPOSE, None, default=Route(p.role, p.effort))
    price = gw.prices.price(res.model)
    in_tok = (len(p.body) + len(render_inputs(inputs))) // 4 + 1
    return (Decimal(in_tok) * price.input + Decimal(p.max_tokens) * price.output) / Decimal(1_000_000)


async def _draft_batch(
    gw: LlmGateway, budget: _Budget, plans: Sequence[Plan]
) -> tuple[dict[str, Drafted], list[Plan]]:
    inputs = {"items": [draft_inputs(p) for p in plans]}
    worst = _worst_case(gw, inputs)
    budget.reserve(worst)
    try:
        res = await gw.call(
            purpose=PURPOSE,
            lane="background",
            prompt=PROMPT,
            owner_id=None,
            schema=DraftBatch,
            inputs=inputs,
        )
    finally:
        budget.release(worst)
    if res.output is None:
        return {}, list(plans)
    by_id = {d.id: d for d in res.output.items}
    ok: dict[str, Drafted] = {}
    bad: list[Plan] = []
    for p in plans:
        d = by_id.get(p.item.id)
        try:
            if d is None:
                raise ValueError(f"{p.item.id}: missing from the response")
            ok[p.item.id] = check_draft(p, d)
        except ValueError:
            bad.append(p)
    return ok, bad


async def draft_claims(
    gw: LlmGateway, guard: SpendGuard, plans: Sequence[Plan], *, batch_size: int = BATCH_SIZE
) -> dict[str, Drafted]:
    """Draft claims for `plans`: batches run concurrently; an unusable batch is retried once, then we stop."""
    budget = _Budget(guard)
    batches = [plans[i : i + batch_size] for i in range(0, len(plans), batch_size)]
    out: dict[str, Drafted] = {}

    async def one(batch: Sequence[Plan]) -> None:
        todo = list(batch)
        for attempt in (1, 2):
            ok, todo = await _draft_batch(gw, budget, todo)
            out.update(ok)
            if not todo:
                return
            if attempt == 2:
                raise MigrationError(f"drafting failed twice for {[p.item.id for p in todo]}")

    await gather_bounded([one(b) for b in batches])
    return out


# --- assembly ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Converted:
    plan: Plan
    raw: dict[str, Any]  # the v2 item as a yaml-ready dict
    drafted: Drafted | None
    evidence: Evidence | None


def _note(p: Plan, extra: list[str]) -> str:
    it = p.item
    head = f"migrated from v1 {it.id} (category={it.category}, source={it.source}); stratum: {p.rule}"
    parts = [head, *extra]
    if it.category == "unanswerable":
        parts.append(f"absent in the document per v1: {it.expected_answer}")
    if it.category == "conflict":
        parts.append(f"v1 expected_conflict={it.expected_conflict}")
    if it.category == "meta":
        parts.append(
            "answer depends on the exact set of vault documents; recheck against the final fixture set"
        )
    return "; ".join(parts)


def convert_item(p: Plan, drafted: Drafted | None, evidence: Evidence | None) -> Converted:
    it = p.item
    abstain = p.stratum == "unanswerable"
    raw: dict[str, Any] = {
        "id": f"mig-{re.sub(r'[^A-Za-z0-9_.-]+', '-', it.id)}",
        "stratum": p.stratum,
        "question": final_question(it),
        "source_doc": p.source_doc,
        "answer_type": "abstain" if abstain else "answer",
        "audit_status": "draft" if p.keep else "dropped",
    }
    extra: list[str] = []
    if not abstain:
        if drafted is not None:
            raw["required_claims"] = drafted.required
            if drafted.optional:
                raw["optional_claims"] = drafted.optional
            if drafted.alternates:
                raw["alternates"] = drafted.alternates
            extra += ["claims drafted by model (balanced, eval_migrate_claims@1), unaudited", *drafted.notes]
        else:
            raw["required_claims"] = raw_claims(reference_of(it))
            extra.append("claims are the raw v1 reference answer, not drafted")
        if evidence is not None:
            if evidence.ranges:
                raw["gold_evidence"] = evidence.ranges
            extra.append(
                f"gold_evidence: {len(evidence.ranges)} range(s) from {evidence.total} v1 quote(s), "
                f"{evidence.unmapped} unmapped, {evidence.fuzzy} fuzzy, {evidence.ambiguous} ambiguous"
            )
    if it.doc_ids:
        raw["doc_ids"] = list(it.doc_ids)
    if it.category == "multi_turn":
        raw["prior_turns"] = prior_turns(it)
    if it.must_not_contain:
        raw["must_not_contain"] = list(it.must_not_contain)
    if not p.keep:
        extra.insert(0, f"DROPPED: {p.drop_reason}")
    raw["audit_note"] = _note(p, extra)
    return Converted(p, raw, drafted, evidence)


@dataclass
class Migration:
    converted: list[Converted]
    spent: Decimal

    @property
    def kept(self) -> list[Converted]:
        return [c for c in self.converted if c.plan.keep]

    @property
    def dropped(self) -> list[Converted]:
        return [c for c in self.converted if not c.plan.keep]


async def convert(
    items: Sequence[GoldenItem],
    existing: Mapping[str, int],
    gw: LlmGateway,
    guard: SpendGuard,
    *,
    doc_loader: DocLoader | None = None,
    batch_size: int = BATCH_SIZE,
) -> Migration:
    plans = plan_items(items, existing)
    kept = [p for p in plans if p.keep and p.stratum != "unanswerable"]
    drafted = await draft_claims(gw, guard, kept, batch_size=batch_size)
    out: list[Converted] = []
    for p in plans:
        ev = map_evidence(p.item, doc_loader) if p.keep and p.stratum != "unanswerable" else None
        out.append(convert_item(p, drafted.get(p.item.id), ev))
    return Migration(out, guard.spent)


# --- outputs ----------------------------------------------------------------------------------------------


def _trunc(s: str, n: int = 700) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "..."


def render_review(m: Migration) -> str:
    kept, dropped = m.kept, m.dropped
    by_st = Counter(c.plan.stratum for c in kept)
    lines = [
        "# S21.02 migration review (v1 -> v2)",
        "",
        f"Generated by `python -m eval.build.migrate_v1`. {len(m.converted)} v1 items: {len(kept)} converted "
        f"(`audit_status: draft`), {len(dropped)} dropped. Claims were drafted by the model and are "
        "unaudited until S21.05; `gold_evidence` is an approximate document-level sentence range (see the "
        "module docstring), UNVERIFIED against ingested chunks. Dropped items carry the raw v1 answer as "
        "their claims.",
        "",
        "## Converted per stratum",
        "",
        "| stratum | converted | dropped |",
        "|---|---|---|",
    ]
    by_st_drop = Counter(c.plan.stratum for c in dropped)
    for st in sorted({*by_st, *by_st_drop}):
        lines.append(f"| {st} | {by_st[st]} | {by_st_drop[st]} |")
    lines += [
        "",
        f"## Drop list ({len(dropped)})",
        "",
        "| v1 id | stratum | source_doc | reason |",
        "|---|---|---|---|",
    ]
    for c in dropped:
        lines.append(f"| {c.plan.item.id} | {c.plan.stratum} | {c.plan.source_doc} | {c.plan.drop_reason} |")
    lines += ["", f"## Converted items ({len(kept)}): original answer vs drafted claims", ""]
    for c in kept:
        it, raw = c.plan.item, c.raw
        ref = reference_of(it)
        ref_text = ref if isinstance(ref, str) else "; ".join(ref)
        lines += [
            f"### {it.id} -> `{raw['id']}` ({c.plan.stratum}, {raw['answer_type']}, "
            f"source_doc {c.plan.source_doc})",
            "",
            f"Question: {raw['question']}",
            "",
        ]
        if raw.get("prior_turns"):
            lines += ["Prior turns: " + " / ".join(raw["prior_turns"]), ""]
        lines += [f"Original answer: {_trunc(ref_text)}", ""]
        if raw["answer_type"] == "abstain":
            lines += ["Drafted claims: none (abstain item).", ""]
        else:
            lines += ["Drafted required claims:"]
            lines += [f"- {x}" for x in raw["required_claims"]]
            if raw.get("optional_claims"):
                lines += ["", "Optional claims:"] + [f"- {x}" for x in raw["optional_claims"]]
            if raw.get("alternates"):
                lines += ["", "Alternates:"] + [
                    f"- {k} -> {' | '.join(v)}" for k, v in raw["alternates"].items()
                ]
            lines += [""]
        lines += [f"Audit note: {raw['audit_note']}", ""]
    return "\n".join(lines) + "\n"


def write_outputs(m: Migration, out_dir: pathlib.Path) -> None:
    (out_dir / "parts").mkdir(parents=True, exist_ok=True)
    header = (
        "# S21.02 migrated v1 items (generated by eval/build/migrate_v1.py; do not hand-edit before S21.05)\n"
        "# audit_status draft = converted, claims model-drafted and unaudited; dropped = over the cap.\n"
    )
    body = yaml.safe_dump(
        {"items": [c.raw for c in m.converted]}, sort_keys=False, allow_unicode=True, width=120
    )
    (out_dir / PARTS_FILE).write_text(header + body)
    (out_dir / REVIEW_FILE).write_text(render_review(m))


# --- gateways ---------------------------------------------------------------------------------------------


def _dry_gateway(ledger: MemoryLedger) -> LlmGateway:
    from app.core.settings import Settings
    from app.llm.pricing import DEFAULT_PRICES, PriceTable
    from app.llm.prompts import default_registry
    from app.llm.providers.fake import FakeProvider
    from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage

    class DryProvider(FakeProvider):
        """One stub claim per requested item (echoing its reference); no network."""

        async def complete(self, req: ChatRequest) -> ChatResponse:
            self.requests.append(req)
            data = json.loads(
                str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
            )
            items = []
            for it in data["items"]:
                ref = it["reference"]
                text = ref if isinstance(ref, str) else "; ".join(ref)
                items.append(
                    {
                        "id": it["id"],
                        "required_claims": [f"Dry-run claim: {text[:80]}"],
                        "optional_claims": [],
                        "alternates": [],
                    }
                )
            return ChatResponse(
                (TextPart(json.dumps({"items": items})),), "end_turn", Usage(800, 80), None, req.model
            )

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    return LlmGateway(
        chat=DryProvider(),
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings,
    )


def _load_key_from_env_files() -> None:
    """Make OPENAI_API_KEY visible when run from a worktree: read it from the nearest gitignored `.env` up the
    tree (the main checkout's). Only that variable is read, and its value is never printed."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    for base in (ROOT, *ROOT.parents[:4]):
        f = base / ".env"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "OPENAI_API_KEY" and v.strip():
                os.environ["OPENAI_API_KEY"] = v.strip().strip("'\"")
                return


def _live_gateway(ledger: MemoryLedger) -> LlmGateway:
    _load_key_from_env_files()
    # the gateway never touches the database or Redis; Settings only requires the variables to exist
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused/unused")
    os.environ.setdefault("REDIS_URL", "redis://unused")
    from eval.judge import _live_gateway as live

    return live(ledger)


# --- cli --------------------------------------------------------------------------------------------------


def summary(m: Migration, cap: Decimal | None) -> str:
    by_st = Counter(c.plan.stratum for c in m.kept)
    cap_s = f" of cap ${cap}" if cap is not None else ""
    lines = [
        f"items: {len(m.converted)} = {len(m.kept)} converted + {len(m.dropped)} dropped",
        "converted per stratum: " + ", ".join(f"{k}={v}" for k, v in sorted(by_st.items())),
        f"spent ${m.spent}{cap_s}",
    ]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.build.migrate_v1")
    ap.add_argument("--max-usd", type=Decimal, default=None, help="spend cap (mandatory for live calls)")
    ap.add_argument(
        "--dry-run", action="store_true", help="scripted FakeProvider, no network; writes nothing"
    )
    ap.add_argument(
        "--out-dir", type=pathlib.Path, default=None, help="where to write (default: the v2 dataset)"
    )
    ap.add_argument("--no-map", action="store_true", help="skip gold-evidence mapping (no PDF extraction)")
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    a = ap.parse_args(argv)
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    ledger = MemoryLedger()
    gw = _dry_gateway(ledger) if a.dry_run else _live_gateway(ledger)
    guard = SpendGuard(ledger, a.max_usd if a.max_usd is not None else Decimal(1000))
    root = DATASET_V2_DIR
    existing = existing_counts(root)
    loader = None if a.no_map else default_doc_loader()
    try:
        m = asyncio.run(
            convert(load_dataset().items, existing, gw, guard, doc_loader=loader, batch_size=a.batch_size)
        )
    except SpendCapReached as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3
    except MigrationError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 4
    validate_items_v2(
        existing_raw(root) + [c.raw for c in m.converted]
    )  # raises DatasetError listing problems
    out_dir = a.out_dir or (None if a.dry_run else root)
    if out_dir is not None:
        write_outputs(m, out_dir)
    print(summary(m, a.max_usd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
