# ruff: noqa: S324, S311 (sha1 = stable item ids; seeded random = reproducible sampling)
"""Fill the `table_lookup` stratum of the v2 golden set from real FinanceBench 10-Q tables.

Code decides everything that is gold; the model only paraphrases a question.

1. Candidates (code). In the parsed sentence stream of a filing (`eval.build.sample_public.extract_units`,
   the same units every other FinanceBench item indexes) each `table` unit is one row. A row is a candidate
   when it has a label, exactly as many plain numeric cells as the table has identified column headers (dates
   in the table's own header rows, optionally under "Three Months Ended"-style group labels, or dates in the
   heading text above it), a unit in the heading ("in millions"), and a label unique within its table.
   Parenthesised (negative) figures, per-share / percent / count rows and tables without a unit are skipped.
   A table that continues onto the next page keeps its header (`multi-page` in the audit note); a footnote
   marker on the label or the value is kept and flagged (`footnoted`).
2. Question (model, role `balanced` through `app.llm.gateway`, prompt `eval_draft_table_q@1`). It sees the
   company, filing, heading, row label, column label and unit, never the value. A draft is dropped when it
   shares a run of 6+ words with the table text, contains a figure that is not part of the period, or does
   not name the company and a period year.
3. Gold (code). `required_claims` = row label + column + value + unit built from the cell, with number
   alternates (1,577 / 1577 / $1.6 billion); `gold_evidence` = the row's one-sentence range in the stream.

Regenerate (live, capped; reads the gitignored sources, downloads missing PDFs into the cache):

    uv run --project backend python -m eval.build.draft_tables --max-usd 0.30
    uv run --project backend python -m eval.build.draft_tables --dry-run   # FakeProvider, writes nothing

Env: `VAULT_EVAL_SOURCES` overrides the sources directory (default `<repo>/eval/sources`).
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import pathlib
import random
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml
from eval.build.migrate_v1 import _Budget, _live_gateway
from eval.build.sample_public import (
    DEFAULT_CACHE,
    DEFAULT_SOURCES,
    Sources,
    extract_units,
    figure_forms,
    number_alternates,
    slug,
    stream_cache_file,
    write_atomic,
)
from eval.judge import SpendCapReached, SpendGuard, _worst_case_for, gather_bounded
from eval.loader import DATASET_V2_DIR, load_dataset_v2, validate_items_v2
from eval.mapping import norm
from pydantic import BaseModel, ConfigDict

from app.llm.gateway import LlmGateway, MemoryLedger

PURPOSE = "eval_draft"  # no routing-table row: the prompt's front-matter role (balanced) applies
PROMPT = ("eval_draft_table_q", 1)
PARTS_FILE = "parts/tables.yaml"
ID_PREFIX = "tbl_"
STRATUM = "table_lookup"
MAX_PER_DOC = 3
DEFAULT_CANDIDATES = 24
DEFAULT_KEEP = 16
DEFAULT_PER_DOC = 3  # candidates per document sent to the model
BATCH_SIZE = 6
LEAK_RUN = 6  # a question may not share a run of this many words with the table text
# (doc_name, company, doc_period, quarter) of the 10-Qs to draw from, in preference order.
DEFAULT_DOCS: tuple[tuple[str, str, str], ...] = (
    ("3M_2023Q2_10Q", "3M", "Q2 2023"),
    ("APPLE_2023Q3_10Q", "Apple", "Q3 fiscal 2023"),
    ("BESTBUY_2024Q2_10Q", "Best Buy", "Q2 fiscal 2024"),
    ("CORNING_2023Q2_10Q", "Corning", "Q2 2023"),
    ("EBAY_2023Q2_10Q", "eBay", "Q2 2023"),
    ("FOOTLOCKER_2023Q3_10Q", "Foot Locker", "Q3 fiscal 2023"),
    ("LOCKHEEDMARTIN_2023Q2_10Q", "Lockheed Martin", "Q2 2023"),
    ("NETFLIX_2023Q2_10Q", "Netflix", "Q2 2023"),
    ("WALMART_2024Q1_10Q", "Walmart", "Q1 fiscal 2024"),
    ("PAYPAL_2023Q2_10Q", "PayPal", "Q2 2023"),
)


class DraftTablesError(RuntimeError):
    pass


# --- parsing table rows -----------------------------------------------------------------------------------

_MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE = rf"{_MONTHS}\s+\d{{1,2}},?\s+\d{{4}}"
_DATE_RE = re.compile(_DATE)
_GROUP = re.compile(
    r"^(?:(?:three|six|nine|twelve|\d+)\s+months?\s+ended|(?:fiscal\s+)?years?\s+ended)$",
    re.I,
)
_YEAR_CELL = re.compile(r"^(?:19|20)\d\d$")
_NUMBER = re.compile(r"^\$?\s*(\d[\d,]*(?:\.\d+)?)$")
_FOOT_VALUE = re.compile(r"^(\$?\s*\d[\d,]*(?:\.\d+)?)\s*(?:\(\w{1,2}\)|\*+|[¹²³⁴⁵])$")
_FOOT_LABEL = re.compile(r"\s*(?:\(\w{1,2}\)|\*+|[¹²³⁴⁵])\s*$")
_SCALE = re.compile(
    r"\bin\s+(?:us\s+)?(?:\$\s*|dollars\s+in\s+)?(millions|thousands|billions)\b"
    r"|\$\s*in\s+(millions|thousands|billions)\b|\b(millions|thousands|billions)\s+of\s+(?:us\s+)?dollars",
    re.I,
)
_SKIP_LABEL = re.compile(
    r"share|per |%|percent|ratio|rate\b|number of|employee|days|margin|stores|units|basis points|"
    r"weighted|average|\bother\b$|^total$|:\s*$",
    re.I,
)
_STATEMENT = re.compile(
    r"balance sheets?|statements? of (?:earnings|income|operations|cash flows?|comprehensive)|"
    r"financial position|results of operations",
    re.I,
)
_BARE_DASH = {"", "$", "%", ")", "\u2014", "-", "\u2013"}


@dataclass(frozen=True)
class Row:
    idx: int  # unit index in the document sentence stream
    page: int
    label: str
    values: tuple[str, ...]  # plain numeric cells in order, without "$" and footnote markers
    foot_value: bool
    foot_label: bool
    negative: bool  # a parenthesised figure is present
    text: str  # the row as one string (pipes removed)


def _cells(text: str) -> list[str]:
    return [c.strip() for c in text.strip().strip("|").split("|")]


def parse_row(idx: int, page: int, text: str) -> Row | None:
    """A table unit as label + numeric cells; None when a cell is text (not a plain numeric row)."""
    cells = _cells(text)
    if not cells or not cells[0]:
        return None
    values: list[str] = []
    foot_value = neg = False
    for c in cells[1:]:
        if c in _BARE_DASH:
            continue
        if c.startswith("(") and c.endswith(")") and _NUMBER.match(c[1:-1].strip()):
            neg = True
            continue
        if m := _NUMBER.match(c):
            values.append(m.group(1))
        elif (mf := _FOOT_VALUE.match(c)) and (m2 := _NUMBER.match(mf.group(1).strip())):
            values.append(m2.group(1))
            foot_value = True
        else:
            return None
    label = _FOOT_LABEL.sub("", cells[0]).strip()
    return Row(
        idx,
        page,
        label,
        tuple(values),
        foot_value,
        label != cells[0],
        neg,
        " ".join(c for c in cells if c),
    )


# --- headers ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    columns: tuple[str, ...]
    unit: str  # "million" | "thousand" | "billion"
    heading: str  # heading text above the table (prompt context only)


def _unit_of(text: str) -> str | None:
    m = _SCALE.search(text)
    return next(g for g in m.groups() if g).lower().rstrip("s") if m else None


def _columns_from_header_rows(rows: Sequence[list[str]], n: int) -> list[str] | None:
    """Columns from the table's own header rows: a row of dates (or years), optionally under group labels."""
    date_row: list[str] | None = None
    group_row: list[str] | None = None
    for cells in rows:
        items = [c for c in cells[1:] if c and c != "$"]
        if items and all(_DATE_RE.fullmatch(c) or _YEAR_CELL.match(c) for c in items):
            date_row = items
        elif items and all(_GROUP.match(c) for c in items):
            group_row = items
    if not date_row or len(date_row) != n or any(_YEAR_CELL.match(d) for d in date_row):
        return None  # a bare-year column is ambiguous about the period, so it is not a candidate
    if group_row:
        if n % len(group_row):
            return None
        groups = [g for g in group_row for _ in range(n // len(group_row))]
        return [f"{g} {d}" for g, d in zip(groups, date_row, strict=True)]
    return list(date_row)


def _columns_from_context(lines: Sequence[str], n: int) -> list[str] | None:
    """Dates printed as separate lines above the table (a balance sheet): exactly n of them, in order."""
    text = " ".join(" ".join(lines).split())
    if any(len(ln) > 160 for ln in lines):
        return None
    dates = [re.sub(r"\s+", " ", m.group(0)) for m in _DATE_RE.finditer(text)]
    if len(dates) != n or len(set(dates)) != n:
        return None
    groups = [ln.strip() for ln in lines if _GROUP.match(ln.strip())]
    return [f"{groups[0]} {d}" for d in dates] if len(groups) == 1 else dates


def _tail(text: str, n: int = 300) -> str:
    """The last `n` characters of `text`, starting at a word boundary (the nearest heading text)."""
    return text if len(text) <= n else text[-n:].split(" ", 1)[-1]


def _context(units: Sequence[tuple[int, str, str]], start: int, depth: int = 4) -> list[str]:
    """Lines of the heading/paragraph units just above a table run (short ones, stopping at a table)."""
    out: list[str] = []
    k, taken = start - 1, 0
    while k >= 0 and taken < depth and units[k][1] in ("heading", "paragraph") and len(units[k][2]) < 400:
        out = [*units[k][2].splitlines(), *out]
        k -= 1
        taken += 1
    return out


@dataclass(frozen=True)
class Candidate:
    doc_name: str
    doc_key: str  # source_doc / gold doc key
    company: str
    filing: str  # "Form 10-Q (Q2 2023)"
    page: int
    row: Row
    column: str
    unit: str
    heading: str
    value: str
    flags: tuple[str, ...]  # "footnoted", "multi-page"
    statement: bool = False  # the table sits under a primary financial statement title

    @property
    def key(self) -> str:
        return f"{self.doc_key}:{self.row.idx}:{self.column}"


def table_candidates(
    units: Sequence[tuple[int, str, str]], *, doc_name: str, company: str, period: str
) -> list[Candidate]:
    """Every eligible (row, column) cell of one filing; see the module docstring for the rules."""
    doc_key = f"fb_{slug(doc_name)}"
    filing = f"Form 10-Q ({period})"
    out: list[Candidate] = []
    i, n_units = 0, len(units)
    while i < n_units:
        if units[i][1] != "table":
            i += 1
            continue
        j = i
        while j < n_units and units[j][1] == "table":
            j += 1
        start, end = i, j
        i = j
        lines = _context(units, start)
        head_rows = [_cells(units[k][2]) for k in range(start, min(end, start + 5))]
        unit = _unit_of(" ".join(lines)) or next((u for r in head_rows if (u := _unit_of(" ".join(r)))), None)
        if unit is None:
            continue
        rows = [r for k in range(start, end) if (r := parse_row(k, units[k][0], units[k][2])) and r.values]
        if not rows:
            continue
        n = Counter(len(r.values) for r in rows).most_common(1)[0][0]
        cols = _columns_from_header_rows(head_rows, n) or _columns_from_context(lines, n)
        if not cols:
            continue
        labels = Counter(_cells(units[k][2])[0].lower() for k in range(start, end) if units[k][2].strip())
        heading = _tail(" ".join(" ".join(lines).split()))
        statement = bool(_STATEMENT.search(" ".join(_context(units, start, depth=8))))
        first_page = units[start][0]
        for r in rows:
            if (
                len(r.values) != n
                or r.negative
                or _SKIP_LABEL.search(r.label)
                or not re.search(r"[A-Za-z]{3}", r.label)
                or re.search(r"\d", r.label)
                or len(r.label) > 90
                or labels[_cells(units[r.idx][2])[0].lower()] != 1
            ):
                continue
            flags = tuple(
                f
                for f, on in (
                    ("footnoted", r.foot_value or r.foot_label),
                    ("multi-page", r.page != first_page),
                )
                if on
            )
            for col, val in zip(cols, r.values, strict=True):
                if _to_decimal(val) in (None, Decimal(0)):
                    continue
                out.append(
                    Candidate(
                        doc_name,
                        doc_key,
                        company,
                        filing,
                        r.page,
                        r,
                        col,
                        unit,
                        heading,
                        val,
                        flags,
                        statement,
                    )
                )
    return out


def _to_decimal(v: str) -> Decimal | None:
    try:
        return Decimal(v.replace(",", ""))
    except InvalidOperation:
        return None


def select_candidates(
    pool: dict[str, list[Candidate]],
    existing: dict[str, int],
    *,
    seed: int,
    total: int,
    per_doc: int = DEFAULT_PER_DOC,
) -> list[Candidate]:
    """Seeded pick: at most one cell per row, rows per document within the 3-per-document cap (counting the
    set's other items), flagged rows (footnoted / multi-page) first, then primary-statement rows, documents
    round-robin."""
    rng = random.Random(seed)
    queues: dict[str, list[Candidate]] = {}
    for doc, cands in pool.items():
        room = min(per_doc, MAX_PER_DOC - existing.get(cands[0].doc_key, 0)) if cands else 0
        if room <= 0:
            continue
        byrow: dict[int, list[Candidate]] = {}
        for c in cands:
            byrow.setdefault(c.row.idx, []).append(c)
        rows = [rng.choice(v) for _, v in sorted(byrow.items())]
        rng.shuffle(rows)
        rows.sort(key=lambda c: 0 if c.flags else 1 if c.statement else 2)  # stable within each tier
        queues[doc] = rows[:room]
    picked: list[Candidate] = []
    while len(picked) < total and any(queues.values()):
        for doc in sorted(queues):
            if queues[doc] and len(picked) < total:
                picked.append(queues[doc].pop(0))
    return picked


# --- gold built by code -----------------------------------------------------------------------------------


def amount(c: Candidate) -> str:
    return f"${c.value} {c.unit}"


def build_claim(c: Candidate) -> tuple[str, list[str]]:
    """The required claim (row label + column + value + unit) and its accepted number variants."""
    claim = f'{c.company} reports "{c.row.label}" of {amount(c)} for the column "{c.column}" ({c.filing}).'
    alts = number_alternates(c.value, c.unit)
    v = _to_decimal(c.value)
    if c.unit == "million" and v is not None and v >= 1000:
        rounded = f"${round(v / 1000, 1):,} billion"
        if rounded not in alts:
            alts.append(rounded)
    if c.value not in alts:
        alts.append(c.value)
    # variants must not be a bare year/date that could match anything: keep numeric-looking forms only
    return claim, [a for a in dict.fromkeys(alts) if a != claim]


def audit_note(c: Candidate) -> str:
    parts = [
        f'S21.04b table cell: {c.doc_name} page {c.page} row "{c.row.label}" (unit {c.row.idx}) '
        f'col "{c.column}" unit {c.unit}s',
        "value, row, column and unit chosen by code from the parsed table; question drafted by model "
        "(balanced, eval_draft_table_q@1), unaudited",
    ]
    if "footnoted" in c.flags:
        parts.append("footnoted: the label or value carries a footnote marker")
    if "multi-page" in c.flags:
        parts.append("multi-page table: the row is on a later page than the table's header")
    return "; ".join(parts)


def item_id(c: Candidate) -> str:
    return f"{ID_PREFIX}{STRATUM}_{hashlib.sha1(c.key.encode()).hexdigest()[:10]}"


def to_item(c: Candidate, question: str) -> dict[str, Any]:
    claim, alts = build_claim(c)
    item: dict[str, Any] = {
        "id": item_id(c),
        "stratum": STRATUM,
        "question": question,
        "source_doc": c.doc_key,
        "answer_type": "answer",
        "audit_status": "draft",
        "required_claims": [claim],
    }
    if alts:
        item["alternates"] = {claim: alts}
    item["gold_evidence"] = [{"doc": c.doc_key, "sentence_start": c.row.idx, "sentence_end": c.row.idx + 1}]
    item["doc_ids"] = [c.doc_key]
    item["audit_note"] = audit_note(c)
    return item


# --- the model's part: a paraphrased question -------------------------------------------------------------


class DraftQ(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    question: str


class DraftBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DraftQ]


def draft_inputs(c: Candidate) -> dict[str, Any]:
    return {
        "id": item_id(c),
        "company": c.company,
        "filing": c.filing,
        "table_heading": c.heading,
        "row_label": c.row.label,
        "column_label": c.column,
        "unit": f"USD {c.unit}s",
    }


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(s))


def shares_run(question: str, source: str, n: int = LEAK_RUN) -> bool:
    q, s = _words(question), _words(source)
    grams = {tuple(s[i : i + n]) for i in range(len(s) - n + 1)}
    return any(tuple(q[i : i + n]) in grams for i in range(len(q) - n + 1))


_NUM_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def check_question(c: Candidate, q: str) -> str | None:
    """Why a drafted question is unusable (None when fine): leak, figure, or missing company/period."""
    q = q.strip()
    if not q or len(q) > 300 or "\n" in q:
        return "empty, multi-line or too long"
    if shares_run(q, f"{c.row.text} {c.heading}"):
        return f"shares a run of {LEAK_RUN}+ words with the table text"
    allowed = {t.replace(",", "") for t in _NUM_TOKEN.findall(f"{c.column} {c.filing} {c.company}")}
    stray = {t.replace(",", "") for t in _NUM_TOKEN.findall(q)} - allowed
    if stray:
        return f"contains figure(s) {sorted(stray)[:3]}"
    for form in figure_forms(c.value):
        if re.search(rf"(?<![\d,.]){re.escape(form)}(?![\d,]|\.\d)", q):
            return "contains the answer figure"
    if c.company.split()[0].lower() not in q.lower():
        return "does not name the company"
    years = re.findall(r"\b(?:19|20)\d\d\b", c.column)
    if years and not any(y in q for y in years):
        return "does not name the period year"
    return None


async def draft_questions(
    gw: LlmGateway,
    guard: SpendGuard,
    cands: Sequence[Candidate],
    *,
    batch_size: int = BATCH_SIZE,
) -> dict[str, str]:
    """One call per batch of candidates, concurrently under a reserved budget; an incomplete batch is retried
    once, then we stop."""
    budget = _Budget(guard)
    by_id = {item_id(c): c for c in cands}
    out: dict[str, str] = {}

    async def call(batch: Sequence[Candidate]) -> dict[str, str]:
        inputs = {"items": [draft_inputs(c) for c in batch]}
        worst = _worst_case_for(gw, PROMPT, inputs, None)
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
            return {}
        return {d.id: d.question for d in res.output.items if d.id in by_id}

    async def one(batch: Sequence[Candidate]) -> None:
        todo = list(batch)
        for attempt in (1, 2):
            got = await call(todo)
            out.update(got)
            todo = [c for c in todo if item_id(c) not in got]
            if not todo:
                return
            if attempt == 2:
                raise DraftTablesError(f"drafting failed twice for {[item_id(c) for c in todo]}")

    await gather_bounded([one(cands[i : i + batch_size]) for i in range(0, len(cands), batch_size)])
    return out


# --- assembly ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    items: list[dict[str, Any]]
    rejected: list[tuple[str, str]]  # (candidate id, reason)
    candidates: int
    spent: Decimal


async def build_items(
    gw: LlmGateway,
    guard: SpendGuard,
    cands: Sequence[Candidate],
    *,
    keep: int = DEFAULT_KEEP,
    batch_size: int = BATCH_SIZE,
) -> Result:
    qs = await draft_questions(gw, guard, cands, batch_size=batch_size)
    items: list[dict[str, Any]] = []
    rejected: list[tuple[str, str]] = []
    for c in cands:
        q = qs.get(item_id(c), "")
        why = check_question(c, q)
        if why:
            rejected.append((item_id(c), why))
        elif len(items) < keep:
            items.append(to_item(c, q.strip()))
    return Result(items, rejected, len(cands), guard.spent)


# --- documents --------------------------------------------------------------------------------------------


def load_units(pdf: pathlib.Path, cache: pathlib.Path) -> list[tuple[int, str, str]]:
    """The filing's raw sentence-stream units (page, kind, text), from the parsed-document cache."""
    cache.mkdir(parents=True, exist_ok=True)
    f = stream_cache_file(pdf, cache)
    if f.exists():
        return [(int(p), str(k), str(t)) for p, k, t in json.loads(gzip.decompress(f.read_bytes()))]
    units = extract_units(pdf)
    write_atomic(f, gzip.compress(json.dumps(units).encode()))
    return units


def existing_counts(root: pathlib.Path) -> dict[str, int]:
    """Non-dropped items per source_doc in the dataset, excluding this script's own previous output."""
    ds = load_dataset_v2(root)
    c = Counter(
        i.source_doc for i in ds.items if i.audit_status != "dropped" and not i.id.startswith(ID_PREFIX)
    )
    return dict(c)


def existing_raw(root: pathlib.Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    files = [
        root / "golden.yaml",
        *sorted((root / "parts").glob("*.yaml")),
        *sorted((root / "authored").glob("*.yaml")),
    ]
    for f in files:
        if f.exists() and f != root / PARTS_FILE:
            out += (yaml.safe_load(f.read_text()) or {}).get("items", [])
    return out


def gather_pool(
    sources: Sources, docs: Sequence[tuple[str, str, str]], cache: pathlib.Path
) -> dict[str, list[Candidate]]:
    pool: dict[str, list[Candidate]] = {}
    for name, company, period in docs:
        pdf = sources.fb_pdf(name)
        units = load_units(pdf, cache / "streams")
        pool[name] = table_candidates(units, doc_name=name, company=company, period=period)
    return pool


# --- gateways, cli ----------------------------------------------------------------------------------------


def _dry_gateway(ledger: MemoryLedger) -> LlmGateway:
    from app.core.settings import Settings
    from app.llm.pricing import DEFAULT_PRICES, PriceTable
    from app.llm.prompts import default_registry
    from app.llm.providers.fake import FakeProvider
    from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage

    class DryProvider(FakeProvider):
        """A templated question per requested item; no network."""

        async def complete(self, req: ChatRequest) -> ChatResponse:
            self.requests.append(req)
            data = json.loads(
                str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
            )
            items = [
                {
                    "id": it["id"],
                    "question": f"What did {it['company']} report for {it['row_label'].lower()} "
                    f"for the column {it['column_label']}?",
                }
                for it in data["items"]
            ]
            return ChatResponse(
                (TextPart(json.dumps({"items": items})),),
                "end_turn",
                Usage(800, 80),
                None,
                req.model,
            )

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    return LlmGateway(
        chat=DryProvider(),
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings,
    )


def summary(r: Result, cap: Decimal | None, per_doc: Counter[str]) -> str:
    cap_s = f" of cap ${cap}" if cap is not None else ""
    flags = Counter(f for it in r.items for f in ("footnoted", "multi-page") if f in it["audit_note"])
    return "\n".join(
        [
            f"candidates drafted: {r.candidates}; kept {len(r.items)}; rejected {len(r.rejected)}",
            "rejected: " + "; ".join(f"{i}: {w}" for i, w in r.rejected[:10]),
            "kept per document: " + ", ".join(f"{k}={v}" for k, v in sorted(per_doc.items())),
            f"flags: footnoted={flags['footnoted']} multi-page={flags['multi-page']}",
            f"spent ${r.spent}{cap_s}",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.build.draft_tables")
    ap.add_argument(
        "--max-usd",
        type=Decimal,
        default=None,
        help="spend cap (mandatory for live calls)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="scripted FakeProvider, no network; writes nothing",
    )
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--candidates", type=int, default=DEFAULT_CANDIDATES)
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--sources", type=pathlib.Path, default=DEFAULT_SOURCES)
    ap.add_argument("--cache-dir", type=pathlib.Path, default=DEFAULT_CACHE)
    ap.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=None,
        help="dataset root (default: the v2 dataset)",
    )
    a = ap.parse_args(argv)
    if not a.dry_run and a.max_usd is None:
        print("refusing live calls without an explicit --max-usd cap", file=sys.stderr)
        return 2
    root = a.out_dir or DATASET_V2_DIR
    ledger = MemoryLedger()
    guard = SpendGuard(ledger, a.max_usd if a.max_usd is not None else Decimal(1000))
    pool = gather_pool(Sources(a.sources, a.cache_dir), DEFAULT_DOCS, a.cache_dir)
    cands = select_candidates(pool, existing_counts(root), seed=a.seed, total=a.candidates)
    if len(cands) < 15 and not a.dry_run:
        print(f"only {len(cands)} candidates; need 15+", file=sys.stderr)
        return 5
    gw = _dry_gateway(ledger) if a.dry_run else _live_gateway(ledger)
    try:
        r = asyncio.run(build_items(gw, guard, cands, keep=a.keep))
    except SpendCapReached as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3
    except DraftTablesError as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 4
    validate_items_v2(existing_raw(root) + r.items)  # raises DatasetError listing every problem
    if not a.dry_run or a.out_dir is not None:
        out = root / PARTS_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "# S21.04b: table_lookup items from eval/build/draft_tables.py (gold unaudited until S21.05)\n"
            + yaml.safe_dump({"items": r.items}, sort_keys=False, allow_unicode=True, width=110)
        )
    print(summary(r, a.max_usd, Counter(it["source_doc"] for it in r.items)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
