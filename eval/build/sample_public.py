# ruff: noqa: S324, S311, S310, S603 (sha1 = stable ids, seeded random = reproducible sampling, fixed https URLs)
"""Sample strata 1-6 of the v2 golden set from the public sets (plan S21.04): FinanceBench, QASPER, CUAD.

$0: no model calls. Everything here is rule-based and seeded.

Regenerate (reads the gitignored sources, downloads missing PDFs into the cache directory):

    cd backend && uv run python ../eval/build/sample_public.py --seed 21 \
        [--sources /path/to/eval/sources] [--cache-dir DIR] [--out PATH]

Env: `VAULT_EVAL_SOURCES` overrides the sources directory (default `<repo>/eval/sources`).

Gold evidence is document-level sentence ranges `[sentence_start, sentence_end)` over the *document sentence
stream*: `app.ingestion.chunk._units` over `ExtractedDocument.elements` in order, i.e. the chunker@1 sentences
without chunk overlap. The `doc` key is the item's `source_doc`. A quote is located with `eval.mapping`'s
normalization and thresholds (exact normalized match, word-bounded; fuzzy `partial_ratio >= FUZZ_MIN` only for
quotes of 25+ characters). An item is kept only when every gold quote maps (`gold_unmapped == 0`).

Classification rules (deterministic; one stratum per candidate):

FinanceBench (`fb_stratum`, then confirmed against the gold page), `short_numeric` = the answer has a digit
and is under 40 characters, `COMPUTE` = the question contains a computation word (ratio, change,
difference, growth, percent, average, sum of, total of, margin, turnover, year-over-year, increase,
decrease, calculate, "defined as", "%", ...):
  - short_numeric, reasoning not "logical", question has a COMPUTE word      -> numeric_calc
  - short_numeric, reasoning not "logical", no COMPUTE word                  -> table_lookup candidate,
    kept only if the answer figure appears verbatim (number-bounded: 8.7 does not match 18.75; "1577.00"
    matches "1,577") in a unit of the gold page; its gold is then narrowed to those units (up to 3, table
    rows first). A candidate whose figure is not verbatim on the page is a derived value and is DEMOTED to
    numeric_calc (`Cand.demote`), never padded into table_lookup.
  - not short_numeric, not metrics-generated, no numerical/logical reasoning, one evidence page,
    answer <= 2 sentences and <= 240 chars                                  -> single_span
  numeric_calc / single_span gold stays page-level because FinanceBench evidence is a whole page
  (noted in `audit_note`).
QASPER (`qasper_stratum`), first answer, highlighted evidence (table/figure "FLOAT SELECTED" skipped):
  - evidence in >= 2 distinct sections (abstract counts as one)        -> multi_hop
  - else >= 2 extractive spans                                         -> multi_span
  - else exactly one evidence section and a short answer               -> single_span
CUAD (`cuad_stratum`), by clause category and distinct expert spans:
  - conditional categories whose span contains a conditional marker (unless, provided that, subject to,
    notice, consent, ...)                                              -> conditional
  - list categories with 2-6 distinct spans                            -> multi_span
  - date / governing-law categories with one span                      -> single_span

`alternates` hold bare value variants (number formats, units, dates) derived by rule; the key is the claim.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.request
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml
from rapidfuzz import fuzz

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eval.mapping import FUZZ_MIN, REF, norm  # noqa: E402

DEFAULT_SOURCES = pathlib.Path(os.environ.get("VAULT_EVAL_SOURCES", ROOT / "eval" / "sources"))
DEFAULT_CACHE = ROOT / "eval" / "sources" / "_v2_cache"
DEFAULT_OUT = ROOT / "eval" / "datasets" / "vault_eval_v2" / "parts" / "public.yaml"
DEFAULT_SEED = 21
MAX_PER_DOC = 3
TARGETS = {
    "single_span": 40,
    "conditional": 20,
    "table_lookup": 25,
    "numeric_calc": 20,
    "multi_span": 20,
    "multi_hop": 20,
}
# Scarcest source first, so documents are not used up by the flexible strata.
ORDER = ["multi_hop", "table_lookup", "numeric_calc", "conditional", "multi_span", "single_span"]
# Per-stratum, per-source quota (sum >= target; a stratum stops at its target).
QUOTAS = {
    "single_span": {"cuad": 16, "qasper": 16, "fb": 8},
    "multi_span": {"cuad": 10, "qasper": 10},
}
PER_CATEGORY_CAP = {"conditional": 4, "multi_span": 3, "single_span": 5}
FETCH_UA = "vault-eval-fetch"

# ----------------------------------------------------------------------------------------------------------
# candidates


@dataclass
class Cand:
    key: str  # stable identity (hashed into the item id)
    stratum: str
    src: str  # fb | qasper | cuad
    doc: str  # source_doc and gold doc key
    pdf: Callable[[], pathlib.Path] | None
    question: str
    claims: list[str]
    quotes: list[tuple[str, int | None]]  # (verbatim quote, page hint 1-based)
    optional: list[str] = field(default_factory=list)
    alternates: dict[str, list[str]] = field(default_factory=dict)
    category: str = ""  # per-category cap (CUAD)
    note: str | None = None
    figures: list[str] = field(default_factory=list)  # FinanceBench table_lookup: figure strings to find
    narrow: bool = False
    demote: Callable[[], Cand | None] | None = None  # table_lookup whose gold has no such row -> numeric_calc
    ref: str = ""  # "fb:<doc_name>" | "qasper:<arxiv id>" | "cuad:<title>": what a warm worker resolves


def slug(s: str, n: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n]


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", REF.sub("", s)).strip()


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$(\"'])|\s*\n+\s*(?:[-•*]\s*)?")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text.strip()) if s and s.strip()]


# -- numbers ---------------------------------------------------------------------------------------------

_NUM = re.compile(
    r"(\()?\s*(-)?\s*\$?\s*(-)?(\d[\d,]*(?:\.\d+)?)\s*\)?\s*(%|percent|million|billion|thousand)?", re.I
)
_SCALE_Q = re.compile(r"\bin\s+(?:usd\s+|us\$\s*|\$\s*)?(millions|billions|thousands)\b", re.I)


def _fmt(v: Decimal, decimals: int | None = None) -> str:
    if decimals is None:
        decimals = 0 if v == v.to_integral() else max(0, -v.normalize().as_tuple().exponent)  # type: ignore[operator]
    return f"{v:,.{decimals}f}"


def number_alternates(answer: str, unit: str | None = None) -> list[str]:
    """Bare variants of the first number in `answer` (commas, currency, scale words, percent). `unit` is the
    scale word the question states ("million"); returns [] when no number is found."""
    m = _NUM.search(answer)
    if not m:
        return []
    try:
        v = Decimal(m.group(4).replace(",", ""))
    except InvalidOperation:
        return []
    neg = bool(m.group(2) or m.group(3) or m.group(1))
    word = (m.group(5) or unit or "").lower()
    sign = "-" if neg else ""
    out: list[str] = []
    if word in ("%", "percent"):
        for num in {_fmt(v), _fmt(v.normalize() if v != v.to_integral() else v)}:
            out += [f"{sign}{num}%", f"{sign}{num} percent"]
    else:
        nums = {_fmt(v), f"{v:f}", str(v.normalize()) if v != v.to_integral() else str(int(v))}
        if v == v.to_integral():
            nums |= {f"{int(v):,}", str(int(v))}
        suffixes = [""] if not word else [f" {word}", ""]
        for num in sorted(nums):
            for suf in suffixes:
                out += [f"{sign}{num}{suf}", f"{sign}${num}{suf}"]
        if word == "million" and v >= 1000:
            b = v / 1000
            out += [f"{sign}${_fmt(b.normalize())} billion", f"{sign}{_fmt(b.normalize())} billion"]
        if word == "billion":
            out += [f"{sign}${_fmt(v * 1000)} million", f"{sign}{_fmt(v * 1000)} million"]
    seen: set[str] = set()
    res = []
    for a in out:
        if a not in seen and a.strip() != answer.strip():
            seen.add(a)
            res.append(a)
    return res[:8]


def figure_forms(answer: str) -> list[str]:
    """Strings a table row would contain for a numeric answer ("$1577.00" -> "1,577", "1577")."""
    m = _NUM.search(answer)
    if not m:
        return []
    try:
        v = Decimal(m.group(4).replace(",", ""))
    except InvalidOperation:
        return []
    forms = {m.group(4), _fmt(v), f"{v:f}"}
    if v != v.to_integral():
        forms |= {str(v.normalize()), _fmt(v.normalize())}
    else:
        forms |= {f"{int(v):,}", str(int(v))}
    return sorted((f for f in forms if len(f.replace(",", "").replace(".", "")) >= 2), key=len, reverse=True)


# -- FinanceBench ----------------------------------------------------------------------------------------


COMPUTE = re.compile(
    r"\b(ratio|change|changed|difference|differ|growth|grew|percent|percentage|average|avg|sum of|total of|"
    r"combined|margin|turnover|yoy|year-over-year|increase|decrease|decline|calculate|compute|"
    r"defined as|conversion|per share growth|cagr|compared? (?:to|with)|less|more than)\b|%",
    re.I,
)


def fb_stratum(row: dict[str, Any]) -> str | None:
    """table_lookup needs a verified gold row, so the classifier only proposes it; `map_cand` confirms that
    the answer figure appears verbatim in a unit of the gold page, else the candidate is demoted to
    numeric_calc (`Cand.demote`)."""
    ans = row["answer"].strip()
    r = (row.get("question_reasoning") or "").lower()
    short_numeric = bool(re.search(r"\d", ans)) and len(ans) < 40
    logical = "logical" in r
    if short_numeric and not logical:
        return "numeric_calc" if COMPUTE.search(row.get("question", "")) else "table_lookup"
    if (
        row["question_type"] != "metrics-generated"
        and "numerical reasoning" not in r
        and not logical
        and len(row["evidence"]) == 1
        and len(ans) <= 240
        and len(sentences(ans)) <= 2
        and not short_numeric
    ):
        return "single_span"
    return None


def fb_cand(row: dict[str, Any], stratum: str, pdf_for: Callable[[str], pathlib.Path]) -> Cand | None:
    ans = row["answer"].strip()
    doc = f"fb_{slug(row['doc_name'])}"
    ev = [
        (clean_text(e["evidence_text"]), (e.get("evidence_page_num") or 0) + 1)
        for e in row["evidence"]
        if e.get("evidence_text")
    ]
    if not ev or len(ev) > 3:
        return None
    alternates: dict[str, list[str]] = {}
    optional: list[str] = []
    figures: list[str] = []
    if stratum in ("table_lookup", "numeric_calc"):
        unit_m = _SCALE_Q.search(row["question"])
        unit = unit_m.group(1).lower().rstrip("s") if unit_m else None
        shown = ans if not unit or re.search(r"million|billion|thousand|%", ans, re.I) else f"{ans} {unit}"
        claim = f"The answer is {shown}."
        claims = [claim]
        alt = number_alternates(ans, unit)
        if alt:
            alternates[claim] = alt
        if stratum == "table_lookup":
            figures = figure_forms(ans)
        just = clean_text(row.get("justification") or "")
        if stratum == "numeric_calc" and re.search(r"[=/]|divid|\bsum\b|\bper\b|\bminus\b", just):
            optional = [s for s in sentences(just) if len(s.split()) >= 4][:2]
    else:
        claims = [s for s in (clean_text(x) for x in sentences(ans)) if len(s.split()) >= 2][:6]
        if not claims:
            return None
    note = None
    if stratum != "table_lookup":
        note = "gold is page-level: FinanceBench evidence is a whole page"
    return Cand(
        key=row["financebench_id"],
        stratum=stratum,
        src="fb",
        doc=doc,
        pdf=lambda n=row["doc_name"]: pdf_for(n),
        ref=f"fb:{row['doc_name']}",
        question=row["question"].strip(),
        claims=claims,
        quotes=ev,
        optional=optional,
        alternates=alternates,
        note=note,
        figures=figures,
        narrow=stratum == "table_lookup",
        demote=(lambda: fb_cand(row, "numeric_calc", pdf_for)) if stratum == "table_lookup" else None,
    )


# -- QASPER ----------------------------------------------------------------------------------------------


def _paragraph_index(paper: dict[str, Any]) -> list[tuple[str, str]]:
    out = [("Abstract", clean_text(paper.get("abstract") or ""))]
    for sec in paper["full_text"]:
        for p in sec["paragraphs"]:
            out.append((sec["section_name"] or "", clean_text(p)))
    return out


def qasper_section(text: str, index: list[tuple[str, str]]) -> str | None:
    t = clean_text(text)
    if not t:
        return None
    for name, para in index:
        if t in para:
            return name or "(untitled)"
    return None


def qasper_answer_claims(a: dict[str, Any]) -> list[str]:
    if a.get("yes_no") is not None:
        return ["The answer is yes."] if a["yes_no"] else ["The answer is no."]
    if a.get("extractive_spans"):
        spans = [clean_text(s) for s in a["extractive_spans"]]
        return [f"The answer includes: {s}" for s in dict.fromkeys(spans) if len(re.sub(r"\W", "", s)) >= 2]
    ff = clean_text(a.get("free_form_answer") or "")
    return [s for s in sentences(ff) if len(s.split()) >= 2]


def qasper_stratum(a: dict[str, Any], sections: list[str]) -> str | None:
    """`sections`: the section of each highlighted evidence sentence (None when it could not be placed)."""
    if a.get("unanswerable") or not sections or any(s is None for s in sections):
        return None
    n_sections = len(set(sections))
    spans = [s for s in (a.get("extractive_spans") or []) if clean_text(s)]
    claims = qasper_answer_claims(a)
    if not claims or len(claims) > 6:
        return None
    if n_sections >= 2:
        return "multi_hop"
    if len(set(clean_text(s) for s in spans)) >= 2:
        return "multi_span"
    if n_sections == 1 and len(claims) == 1 and len(claims[0]) <= 300:
        return "single_span"
    return None


def qasper_cands(papers: dict[str, Any], pdf_for: Callable[[str], pathlib.Path]) -> list[Cand]:
    out: list[Cand] = []
    for pid, p in papers.items():
        index = _paragraph_index(p)
        for qa in p["qas"]:
            a = qa["answers"][0]["answer"]
            ev = [
                e
                for e in (a.get("highlighted_evidence") or [])
                if clean_text(e) and "FLOAT SELECTED" not in e
            ]
            if not ev or any("FLOAT SELECTED" in e for e in (a.get("evidence") or [])):
                continue
            sections = [qasper_section(e, index) for e in ev]
            st = qasper_stratum(a, sections)  # type: ignore[arg-type]
            if st is None:
                continue
            claims = qasper_answer_claims(a)
            out.append(
                Cand(
                    key=f"{pid}:{qa['question_id']}",
                    stratum=st,
                    src="qasper",
                    doc=f"qasper_{pid.replace('.', '_')}",
                    pdf=lambda i=pid: pdf_for(i),
                    ref=f"qasper:{pid}",
                    question=qa["question"].strip(),
                    claims=list(dict.fromkeys(claims)),
                    quotes=[(clean_text(e), None) for e in ev[:6]],
                )
            )
    return out


# -- CUAD ------------------------------------------------------------------------------------------------

CUAD_QUESTIONS: dict[str, list[str]] = {
    "Agreement Date": [
        "On what date was this agreement signed or dated?",
        "What is the date of the agreement?",
        "When was this contract entered into?",
    ],
    "Effective Date": [
        "From what date does this agreement take effect?",
        "What is the effective date of the contract?",
        "When do the obligations under this agreement begin?",
    ],
    "Expiration Date": [
        "When does this agreement end?",
        "What is the stated term or expiration of the contract?",
        "For how long does the agreement run, or on what date does it expire?",
    ],
    "Governing Law": [
        "Which jurisdiction's law governs this agreement?",
        "Under the law of which state or country is this contract interpreted?",
        "What governing-law provision does the contract contain?",
    ],
    "Termination For Convenience": [
        "Under what conditions can a party end this agreement without cause?",
        "When may the contract be terminated for convenience, and with what notice?",
        "What must a party do to terminate this agreement without alleging a breach?",
    ],
    "Change Of Control": [
        "What happens if one of the parties undergoes a change of control?",
        "Under what circumstances does a merger or change in ownership affect this agreement?",
        "What consequences follow if control of a party changes hands?",
    ],
    "Anti-Assignment": [
        "Under what conditions may this agreement be assigned or transferred?",
        "Can a party hand its rights under the contract to someone else, and when?",
        "What restrictions apply to assigning this agreement, and are there exceptions?",
    ],
    "Notice Period To Terminate Renewal": [
        "How much notice is required to prevent the agreement from renewing?",
        "What is the deadline for opting out of renewal?",
        "When and how must a party give notice that it will not renew?",
    ],
    "Competitive Restriction Exception": [
        "What exceptions apply to the competitive restrictions in this agreement?",
        "Under what conditions is a party allowed to compete despite the restriction?",
        "Which carve-outs soften the non-compete or exclusivity limits?",
    ],
    "Post-Termination Services": [
        "What must the parties still do after the agreement ends, and under what conditions?",
        "Which obligations survive termination or expiry of the contract?",
        "Are any services or duties required after termination, and on what terms?",
    ],
    "Renewal Term": [
        "Under what conditions does the agreement renew, and for how long?",
        "How does the contract extend beyond its initial term?",
        "What are the renewal terms of this agreement?",
    ],
    "Liquidated Damages": [
        "In what circumstances must a party pay a pre-agreed sum for breach?",
        "Under what conditions are liquidated damages owed, and how much?",
        "What fixed damages does the contract set for a breach?",
    ],
    "Parties": [
        "List all parties to this agreement.",
        "Who are the contracting parties named in the contract?",
        "Name every party that signed or is bound by this agreement.",
    ],
    "License Grant": [
        "List the licenses or rights granted under this agreement.",
        "What permissions does the contract grant, item by item?",
        "Enumerate the rights one party gives the other here.",
    ],
    "Audit Rights": [
        "List every audit or inspection right in this agreement.",
        "Which audit and record-inspection rights does the contract provide?",
        "What rights does a party have to examine the other's books or facilities?",
    ],
    "Insurance": [
        "List the insurance coverage the agreement requires.",
        "Which kinds of insurance must be maintained under the contract?",
        "What insurance obligations does each party carry?",
    ],
    "Cap On Liability": [
        "List the limits placed on a party's liability.",
        "Which caps or ceilings on damages does the agreement set?",
        "What limitations of liability apply under this contract?",
    ],
    "Exclusivity": [
        "List the exclusivity commitments in this agreement.",
        "Which exclusive rights or dealings does the contract establish?",
        "What exclusivity arrangements bind the parties?",
    ],
    "Minimum Commitment": [
        "List the minimum purchase, volume or payment commitments.",
        "What minimum amounts must a party commit to under the contract?",
        "Which minimum-commitment obligations does the agreement set?",
    ],
    "Revenue/Profit Sharing": [
        "List how revenue or profits are divided between the parties.",
        "Which revenue-sharing or profit-sharing terms appear in the contract?",
        "What share of income does each party receive?",
    ],
    "Ip Ownership Assignment": [
        "List the intellectual-property ownership or assignment provisions.",
        "Who owns the IP created or used under this agreement, per each provision?",
        "Which clauses assign or allocate ownership of intellectual property?",
    ],
    "Non-Compete": [
        "List the non-compete restrictions in this agreement.",
        "What competing activities are restricted under the contract?",
        "Which limits on competition bind the parties?",
    ],
    "Volume Restriction": [
        "List the volume restrictions the agreement imposes.",
        "Which quantity caps or volume limits apply under the contract?",
        "What ceilings on volume are set?",
    ],
    "Non-Transferable License": [
        "List the licence terms that cannot be transferred.",
        "Which licence rights are non-transferable under this agreement?",
        "What transfer limits are placed on the licences granted?",
    ],
    "Covenant Not To Sue": [
        "List the covenants not to sue in this agreement.",
        "Which promises not to bring claims appear in the contract?",
        "What covenants prevent a party from suing the other?",
    ],
}
CUAD_CONDITIONAL = {
    "Termination For Convenience",
    "Change Of Control",
    "Anti-Assignment",
    "Notice Period To Terminate Renewal",
    "Competitive Restriction Exception",
    "Post-Termination Services",
    "Renewal Term",
    "Liquidated Damages",
}
CUAD_SINGLE = {"Agreement Date", "Effective Date", "Expiration Date", "Governing Law"}
CUAD_LIST = {
    "Parties",
    "License Grant",
    "Audit Rights",
    "Insurance",
    "Cap On Liability",
    "Exclusivity",
    "Minimum Commitment",
    "Revenue/Profit Sharing",
    "Ip Ownership Assignment",
    "Non-Compete",
    "Volume Restriction",
    "Non-Transferable License",
    "Covenant Not To Sue",
}
_COND = re.compile(
    r"\b(unless|provided that|provided,? however|subject to|except|if |in the event|upon |prior written|"
    r"written (?:notice|consent)|\d+\)? ?(?:calendar |business )?days?|notice|consent|only (?:if|with|upon))",
    re.I,
)
_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_DATE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_LAW = re.compile(
    r"\blaws?\s+of\s+(?:the\s+)?((?:State|Commonwealth|Province|Republic|People's Republic|Kingdom)\s+of\s+"
    r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?|[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
)


def cuad_stratum(cat: str, spans: list[str]) -> str | None:
    n = len(spans)
    if cat in CUAD_CONDITIONAL and n >= 1 and _COND.search(spans[0]):
        return "conditional"
    if cat in CUAD_LIST and 2 <= n <= 6:
        return "multi_span"
    if cat in CUAD_SINGLE and n == 1:
        return "single_span"
    return None


def date_alternates(span: str) -> list[str]:
    m = _DATE.search(span)
    if not m:
        return []
    mon = _MONTHS.index(m.group(1).capitalize()) + 1
    d, y = int(m.group(2)), m.group(3)
    mon_name = _MONTHS[mon - 1]
    out = [
        f"{y}-{mon:02d}-{d:02d}",
        f"{d} {mon_name} {y}",
        f"{mon_name} {d}, {y}",
        f"{mon}/{d}/{y}",
        f"{mon:02d}/{d:02d}/{y}",
    ]
    return [o for o in out if o.lower() != m.group(0).lower()]


def cuad_claims(cat: str, spans: list[str]) -> tuple[list[str], dict[str, list[str]]] | None:
    alternates: dict[str, list[str]] = {}
    if cat == "Governing Law":
        m = _LAW.search(spans[0])
        if not m:
            return None
        place = m.group(1).strip()
        claim = f"The agreement is governed by the law of {place}."
        short = re.sub(r"^(?:State|Commonwealth|Province|Republic|Kingdom)\s+of\s+", "", place)
        if short != place:
            alternates[claim] = [short, f"{short} law"]
        return [claim], alternates
    if cat in ("Agreement Date", "Effective Date", "Expiration Date"):
        text = clean_text(spans[0])
        if len(text) > 200:
            return None
        lead = {
            "Agreement Date": "The agreement is dated",
            "Effective Date": "The effective date is",
            "Expiration Date": "The term or expiration is stated as",
        }[cat]
        claim = f"{lead} {text}."
        alt = date_alternates(text)
        if alt:
            alternates[claim] = alt
        return [claim], alternates
    claims: list[str] = []
    if cat in CUAD_LIST:
        for s in spans:
            t = clean_text(s)
            if not t or len(t) > 400:
                return None
            claims.append(f"The agreement provides: {t}")
        return list(dict.fromkeys(claims)), {}
    parts = [clean_text(x) for x in sentences(spans[0])]
    parts = [x for x in parts if len(x.split()) >= 5]
    if not parts or any(len(x) > 400 for x in parts[:3]):
        return None
    return [f"The agreement provides: {x}" for x in parts[:3]], {}


def cuad_cands(
    data: list[dict[str, Any]], pdf_for: Callable[[str], pathlib.Path], have_pdf: Callable[[str], bool]
) -> list[Cand]:
    out: list[Cand] = []
    for entry in data:
        title = entry["title"]
        if not have_pdf(title):
            continue
        doc = f"cuad_{slug(title, 40)}_{hashlib.sha1(title.encode()).hexdigest()[:6]}"
        for qa in entry["paragraphs"][0]["qas"]:
            m = re.search(r'related to "([^"]+)"', qa["question"])
            if not m or not qa["answers"]:
                continue
            cat = m.group(1)
            spans = list(dict.fromkeys(clean_text(a["text"]) for a in qa["answers"] if clean_text(a["text"])))
            st = cuad_stratum(cat, spans)
            if st is None or cat not in CUAD_QUESTIONS:
                continue
            made = cuad_claims(cat, spans)
            if made is None:
                continue
            claims, alternates = made
            h = int(hashlib.sha1(f"{title}|{cat}".encode()).hexdigest(), 16)
            qs = CUAD_QUESTIONS[cat]
            out.append(
                Cand(
                    key=f"{title}|{cat}",
                    stratum=st,
                    src="cuad",
                    doc=doc,
                    pdf=lambda t=title: pdf_for(t),
                    ref=f"cuad:{title}",
                    question=qs[h % len(qs)],
                    claims=claims,
                    quotes=[(s, None) for s in spans],
                    alternates=alternates,
                    category=cat,
                )
            )
    return out


# ----------------------------------------------------------------------------------------------------------
# document sentence stream and gold mapping


def _clean_cells(s: str) -> str:
    return norm(s.replace(" | ", " ").replace("| ", " ").replace(" |", " "))


@dataclass
class Stream:
    """Normalized document sentence stream: unit i is sentence i of the document."""

    pages: list[int]
    kinds: list[str]
    texts: list[str]  # normalized
    text: str = ""
    starts: list[int] = field(default_factory=list)
    ends: list[int] = field(default_factory=list)
    page_span: dict[int, tuple[int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.text = " ".join(self.texts)
        pos = 0
        for i, t in enumerate(self.texts):
            if i:
                pos += 1
            self.starts.append(pos)
            pos += len(t)
            self.ends.append(pos)
            lo, hi = self.page_span.get(self.pages[i], (self.starts[-1], self.ends[-1]))
            self.page_span[self.pages[i]] = (min(lo, self.starts[-1]), max(hi, self.ends[-1]))

    def units_in(self, a: int, b: int) -> tuple[int, int]:
        """Half-open unit range overlapping chars [a, b) of `text`."""
        lo = max(0, bisect_right(self.ends, a))  # first unit with end > a
        hi = bisect_right(self.starts, b - 1)  # units with start < b
        lo = min(lo, len(self.texts) - 1)
        return lo, max(hi, lo + 1)


def extract_units(pdf: pathlib.Path) -> list[tuple[int, str, str]]:
    from app.ingestion.chunk import _units
    from app.ingestion.extract.pdf import PdfExtractor

    doc = PdfExtractor().extract(str(pdf))
    out: list[tuple[int, str, str]] = []
    for el in doc.elements:
        page = doc.pages[el.page_index]
        for u in _units(doc, el):
            out.append((page.page_no or 0, el.kind, page.text[u.start : u.end]))
    return out


def stream_cache_file(pdf: pathlib.Path, cache: pathlib.Path) -> pathlib.Path:
    key = hashlib.sha1(f"{pdf}|{pdf.stat().st_size}".encode()).hexdigest()[:16]
    return cache / f"{slug(pdf.stem, 40)}_{key}.json.gz"


def write_atomic(dest: pathlib.Path, data: bytes) -> None:
    """Write via a temp file and rename, so a worker killed on timeout never leaves a partial file."""
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


def load_stream(pdf: pathlib.Path, cache: pathlib.Path, *, build: bool = True) -> Stream:
    """The document's sentence stream from the parsed-document cache; `build=False` (the sampling phase)
    raises FileNotFoundError instead of parsing, so the main process never extracts or downloads."""
    cache.mkdir(parents=True, exist_ok=True)
    f = stream_cache_file(pdf, cache)
    if f.exists():
        units = json.loads(gzip.decompress(f.read_bytes()))
    elif not build:
        raise FileNotFoundError(f.name)
    else:
        units = extract_units(pdf)
        write_atomic(f, gzip.compress(json.dumps(units).encode()))
    return Stream([u[0] for u in units], [u[1] for u in units], [_clean_cells(u[2]) for u in units])


@dataclass(frozen=True)
class Hit:
    lo: int
    hi: int
    n_exact: int
    fuzzy: bool


_LONG, _HEAD = 400, 300


def _word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _exact(q: str, text: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(q), text)]
    bounded = [
        (a, b)
        for a, b in spans
        if (a == 0 or not _word(q[0]) or not _word(text[a - 1]))
        and (b == len(text) or not _word(q[-1]) or not _word(text[b]))
    ]
    return bounded or spans


def locate(st: Stream, quote: str, page: int | None = None) -> Hit | None:
    """Locate one quote in the stream. Several exact hits: the first in reading order (preferring the hinted
    page +-1), flagged via `n_exact`. Fuzzy only for quotes of 25+ characters."""
    q = norm(REF.sub("", quote))
    if not q or not st.texts:
        return None
    probes = [q] if len(q) <= _LONG else [q[:_HEAD], q[-_HEAD:]]
    win = None
    if page is not None:
        spans = [st.page_span[p] for p in (page - 1, page, page + 1) if p in st.page_span]
        if spans:
            win = (min(a for a, _ in spans), max(b for _, b in spans))

    def prefer(hits: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if win is None:
            return hits
        inside = [h for h in hits if win[0] <= h[0] < win[1]]
        return inside or hits

    max_span = len(q) + 800
    per = [prefer(_exact(p, st.text)) for p in probes]
    if all(per):
        if len(probes) == 1:
            a, b = per[0][0]
            return Hit(*st.units_in(a, b), n_exact=len(per[0]), fuzzy=False)
        for ha, _ in per[0]:
            tails = [(ta, tb) for ta, tb in per[1] if tb > ha and tb - ha <= max_span]
            if tails:
                lo, hi = st.units_in(ha, tails[0][1])
                return Hit(lo, hi, n_exact=len(per[0]), fuzzy=False)
    if len(q) < 25:
        return None
    windows = [win] if win else [st.page_span[p] for p in sorted(st.page_span)]
    best: tuple[float, int, int] | None = None
    for a, b in windows:
        seg = st.text[a:b]
        found = []
        for p in probes:
            al = fuzz.partial_ratio_alignment(p, seg)
            if al is None or al.score < FUZZ_MIN:
                break
            found.append((al.score, a + al.dest_start, a + al.dest_end))
        else:
            if len(found) == 2 and not 0 <= found[1][2] - found[0][1] <= max_span:
                continue
            sc = min(f[0] for f in found)
            if best is None or sc > best[0]:
                best = (sc, found[0][1], found[-1][2])
    if best is None:
        return None
    return Hit(*st.units_in(best[1], best[2]), n_exact=0, fuzzy=True)


def merge_ranges(rs: list[tuple[int, int]], gap: int = 0) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(rs):
        if out and lo <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def figure_units(st: Stream, lo: int, hi: int, figures: list[str]) -> list[int]:
    """Units of [lo, hi) holding the answer figure as a whole number (not inside another number). Table rows
    and short units win over long paragraphs (some filings extract their tables as paragraphs); at most 3."""
    pats = [re.compile(r"(?<![\d.,])" + re.escape(f) + r"(?![\d]|,\d)") for f in figures]
    hits = [i for i in range(lo, hi) if st.kinds[i] != "heading" and any(p.search(st.texts[i]) for p in pats)]
    hits.sort(key=lambda i: (st.kinds[i] != "table", len(st.texts[i]) > 400, i))
    return sorted(hits[:3])


def map_cand(c: Cand, st: Stream) -> tuple[list[tuple[int, int]], str | None] | str:
    """Gold evidence ranges (and an ambiguity note) for a candidate, or a rejection reason string."""
    ranges: list[tuple[int, int]] = []
    ambiguous = 0
    for quote, page in c.quotes:
        h = locate(st, quote, page)
        if h is None:
            return "gold_unmapped"
        ambiguous += h.n_exact > 1
        if c.narrow:
            rows = figure_units(st, h.lo, h.hi, c.figures)
            if not rows:
                return "figure_not_in_row"
            ranges += merge_ranges([(i, i + 1) for i in rows], gap=0)
        else:
            ranges.append((h.lo, h.hi))
    note = f"gold: first of several exact occurrences for {ambiguous} quote(s)" if ambiguous else None
    return merge_ranges(ranges), note


# ----------------------------------------------------------------------------------------------------------
# sampling


def doc_order(
    cands: list[Cand], seed: int, stratum: str, rng: random.Random | None = None
) -> tuple[dict[str, list[Cand]], list[str]]:
    """Candidates of a stratum grouped by document, and the seeded document order sampling walks."""
    rng = rng or random.Random(f"{seed}:{stratum}")
    by_doc: dict[str, list[Cand]] = {}
    for c in sorted((c for c in cands if c.stratum == stratum), key=lambda c: c.key):
        by_doc.setdefault(c.doc, []).append(c)
    docs = sorted(by_doc)
    rng.shuffle(docs)
    return by_doc, docs


def sample_stratum(
    cands: list[Cand],
    target: int,
    seed: int,
    stratum: str,
    used: Counter[str],
    mapper: Callable[[Cand], Any],
    *,
    max_per_doc: int = MAX_PER_DOC,
    quotas: dict[str, int] | None = None,
    stats: Counter[str] | None = None,
    rejected: list[tuple[Cand, str]] | None = None,
) -> list[tuple[Cand, Any]]:
    """Seeded, stratified across documents: documents are shuffled, then taken round-robin (one candidate per
    document per pass), skipping documents at the global cap. `mapper` returns a rejection string or the
    gold mapping. Deterministic for a given seed, candidate list and mapper."""
    rng = random.Random(f"{seed}:{stratum}")
    by_doc, docs = doc_order(cands, seed, stratum, rng)
    for d in docs:
        rng.shuffle(by_doc[d])
    stats = stats if stats is not None else Counter()
    kept: list[tuple[Cand, Any]] = []
    per_src: Counter[str] = Counter()
    per_cat: Counter[str] = Counter()
    cat_cap = PER_CATEGORY_CAP.get(stratum)
    depth = 0
    while len(kept) < target and any(len(v) > depth for v in by_doc.values()):
        for d in docs:
            if len(kept) >= target:
                break
            if len(by_doc[d]) <= depth or used[d] >= max_per_doc:
                continue
            c = by_doc[d][depth]
            if quotas and per_src[c.src] >= quotas.get(c.src, 0):
                continue
            if cat_cap and c.category and per_cat[c.category] >= cat_cap:
                continue
            stats["tried"] += 1
            res = mapper(c)
            if isinstance(res, str):
                stats[res] += 1
                if rejected is not None:
                    rejected.append((c, res))
                continue
            kept.append((c, res))
            used[d] += 1
            per_src[c.src] += 1
            per_cat[c.category] += 1
        depth += 1
    stats["kept"] += len(kept)
    return kept


def item_id(c: Cand) -> str:
    return f"pub_{c.stratum}_{hashlib.sha1(c.key.encode()).hexdigest()[:10]}"


def to_item(c: Cand, ranges: list[tuple[int, int]], note: str | None) -> dict[str, Any]:
    notes = [x for x in (c.note, note) if x]
    item: dict[str, Any] = {
        "id": item_id(c),
        "stratum": c.stratum,
        "question": c.question,
        "source_doc": c.doc,
        "answer_type": "answer",
        "audit_status": "draft",
        "required_claims": c.claims,
    }
    if c.optional:
        item["optional_claims"] = c.optional
    if c.alternates:
        item["alternates"] = c.alternates
    item["gold_evidence"] = [{"doc": c.doc, "sentence_start": lo, "sentence_end": hi} for lo, hi in ranges]
    item["doc_ids"] = [c.doc]
    if notes:
        item["audit_note"] = "; ".join(notes)
    return item


# ----------------------------------------------------------------------------------------------------------
# sources and CLI


class Sources:
    def __init__(self, sources: pathlib.Path, cache: pathlib.Path, *, offline: bool = False) -> None:
        self.src, self.cache, self.offline = sources, cache, offline
        self._cuad: dict[str, pathlib.Path] | None = None
        self._streams: dict[pathlib.Path, Stream] = {}

    def cuad_pdfs(self) -> dict[str, pathlib.Path]:
        if self._cuad is None:
            root = self.src / "cuad_full"
            self._cuad = (
                {p.stem: p for p in root.rglob("*") if p.suffix.lower() == ".pdf"} if root.exists() else {}
            )
        return self._cuad

    def _download(self, url: str, dest: pathlib.Path) -> pathlib.Path:
        if dest.exists():
            return dest
        if self.offline:
            raise FileNotFoundError(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                write_atomic(dest, data)
                time.sleep(1.0)
                return dest
            except Exception:
                if attempt == 1:
                    raise
                time.sleep(3 * (attempt + 1))
        raise AssertionError

    def fb_pdf(self, name: str) -> pathlib.Path:
        local = self.src / "fb_pdfs" / f"{name}.pdf"
        if local.exists():
            return local
        url = f"https://raw.githubusercontent.com/patronus-ai/financebench/main/pdfs/{name}.pdf"
        return self._download(url, self.cache / "pdfs" / "fb" / f"{name}.pdf")

    def qasper_pdf(self, pid: str) -> pathlib.Path:
        local = self.src / "qasper_pdfs" / f"{pid}.pdf"
        if local.exists():
            return local
        return self._download(f"https://arxiv.org/pdf/{pid}", self.cache / "pdfs" / "qasper" / f"{pid}.pdf")

    def cuad_pdf(self, title: str) -> pathlib.Path:
        return self.cuad_pdfs()[title]

    def resolve(self, ref: str) -> pathlib.Path:
        kind, _, ident = ref.partition(":")
        return {"fb": self.fb_pdf, "qasper": self.qasper_pdf, "cuad": self.cuad_pdf}[kind](ident)

    def stream(self, pdf: pathlib.Path, *, build: bool = False) -> Stream:
        if pdf not in self._streams:
            if len(self._streams) >= 6:  # bound memory: 10-K streams are large
                self._streams.pop(next(iter(self._streams)))
            self._streams[pdf] = load_stream(pdf, self.cache / "streams", build=build)
        return self._streams[pdf]


def build_candidates(s: Sources) -> list[Cand]:
    cands: list[Cand] = []
    fb_path = s.src / "fb_questions.jsonl"
    if fb_path.exists():
        for line in fb_path.read_text().splitlines():
            row = json.loads(line)
            st = fb_stratum(row)
            c = fb_cand(row, st, s.fb_pdf) if st else None
            if c:
                cands.append(c)
    papers: dict[str, Any] = {}
    for f in ("qasper-dev-v0.3.json", "qasper-train-v0.3.json"):
        if (s.src / f).exists():
            papers.update(json.loads((s.src / f).read_text()))
    cands += qasper_cands(papers, s.qasper_pdf)
    cuad_json = s.src / "cuad" / "CUADv1.json"
    if cuad_json.exists():
        cands += cuad_cands(
            json.loads(cuad_json.read_text())["data"], s.cuad_pdf, lambda t: t in s.cuad_pdfs()
        )
    return cands


WARM_TIMEOUT_S = 60
WARM_WORKERS = 3


def warm_one(sources: pathlib.Path, cache: pathlib.Path, ref: str) -> None:
    """Worker entry: download (if needed) and parse one document into the stream cache."""
    s = Sources(sources, cache)
    s.stream(s.resolve(ref), build=True)


def warm(
    s: Sources, cands: list[Cand], seed: int, *, workers: int = WARM_WORKERS, timeout: int = WARM_TIMEOUT_S
) -> dict[str, str]:
    """Parse the documents sampling may touch (the first 2*target+10 of each stratum's seeded document order)
    into the cache, `workers` at a time, each in its own subprocess killed after `timeout` seconds. Returns
    ref -> failure reason for documents that were skipped. Progress is printed per document."""
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    refs: dict[str, str] = {}
    for stratum, target in TARGETS.items():
        by_doc, docs = doc_order(cands, seed, stratum)
        for d in docs[: 2 * target + 10]:
            refs.setdefault(by_doc[d][0].ref, d)
    failed: dict[str, str] = {}

    def cached(ref: str) -> bool:
        try:
            return stream_cache_file(s.resolve(ref), s.cache / "streams").exists()
        except Exception:
            return False

    todo = [r for r in sorted(refs) if not cached(r)]
    print(
        f"warm: {len(refs) - len(todo)} cached, {len(todo)} to parse ({workers} workers, {timeout}s each)",
        flush=True,
    )

    def one(ref: str) -> None:
        t0 = time.time()
        cmd = [sys.executable, str(pathlib.Path(__file__).resolve()), "--warm-one", ref]
        cmd += ["--sources", str(s.src), "--cache-dir", str(s.cache)]
        try:
            r = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
            reason = "" if r.returncode == 0 else f"exit {r.returncode}: {r.stderr.strip().splitlines()[-1:]}"
        except subprocess.TimeoutExpired:
            reason = f"timeout>{timeout}s"
        if reason:
            failed[ref] = reason
        print(f"warm {ref[:60]:60} {time.time() - t0:5.1f}s {reason or 'ok'}", flush=True)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))
    return failed


def run(
    sources: pathlib.Path, cache: pathlib.Path, seed: int, no_warm: bool = False
) -> tuple[list[dict[str, Any]], dict[str, Counter[str]]]:
    s = Sources(sources, cache, offline=True)  # sampling reads caches only; `warm` fetches and parses
    cands = build_candidates(s)
    if not no_warm:
        for ref, why in warm(s, cands, seed).items():
            print(f"skipped {ref}: {why}", flush=True)
    used: Counter[str] = Counter()
    stats: dict[str, Counter[str]] = {}

    def mapper(c: Cand) -> Any:
        try:
            pdf = c.pdf() if c.pdf else None
            if pdf is None:
                return "no_pdf"
            st = s.stream(pdf)
        except FileNotFoundError:
            return "doc_not_cached"  # warm skipped it (timeout, 404): reported in the table
        except Exception as e:  # report, do not hide
            return f"pdf_error:{type(e).__name__}"
        return map_cand(c, st)

    items: list[dict[str, Any]] = []
    demoted: list[tuple[Cand, str]] = []
    for stratum in ORDER:
        if (
            stratum == "numeric_calc"
        ):  # table_lookup ran first: its figure-not-in-row rejects are calculations
            for c, why in demoted:
                d = c.demote() if why == "figure_not_in_row" and c.demote else None
                if d:
                    d.note = "demoted from table_lookup: answer figure not verbatim in a gold-page row; " + (
                        d.note or ""
                    )
                    cands.append(d)
        stats[stratum] = Counter()
        kept = sample_stratum(
            cands,
            TARGETS[stratum],
            seed,
            stratum,
            used,
            mapper,
            quotas=QUOTAS.get(stratum),
            stats=stats[stratum],
            rejected=demoted if stratum == "table_lookup" else None,
        )
        stats[stratum]["candidates"] = sum(c.stratum == stratum for c in cands)
        items += [to_item(c, *res) for c, res in kept]
    rank = {n: i for i, n in enumerate(TARGETS)}
    items.sort(key=lambda i: (rank[i["stratum"]], i["id"]))
    return items, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--sources", type=pathlib.Path, default=DEFAULT_SOURCES)
    ap.add_argument("--cache-dir", type=pathlib.Path, default=DEFAULT_CACHE)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--no-warm", action="store_true", help="sample from the parsed-document cache only")
    ap.add_argument("--warm-one", metavar="REF", help="internal: parse one document into the cache")
    a = ap.parse_args()
    if a.warm_one:
        warm_one(a.sources, a.cache_dir, a.warm_one)
        return 0
    items, stats = run(a.sources, a.cache_dir, a.seed, a.no_warm)
    from eval.loader import validate_items_v2

    validate_items_v2(items)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Generated by eval/build/sample_public.py (plan S21.04). Do not edit by hand.\n"
        f"# Regenerate: cd backend && uv run python ../eval/build/sample_public.py --seed {a.seed}\n"
    )
    a.out.write_text(
        header + yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True, width=120)
    )
    by = Counter(i["stratum"] for i in items)
    print(f"{'stratum':14}{'target':>7}{'got':>5}{'pct':>6}   candidates tried rejections")
    for n, t in TARGETS.items():
        st = stats[n]
        rej = {k: v for k, v in st.items() if k not in ("tried", "kept", "candidates")}
        print(f"{n:14}{t:>7}{by[n]:>5}{100 * by[n] // t:>5}%   {st['candidates']:>5} {st['tried']:>5} {rej}")
    print(
        f"total {len(items)} of {sum(TARGETS.values())}; "
        f"docs {len({i['source_doc'] for i in items})}; wrote {a.out}"
    )
    with contextlib.suppress(Exception):
        print("by source:", dict(Counter(i["source_doc"].split("_")[0] for i in items)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
