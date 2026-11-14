"""Fact verification: pure functions, no model, no I/O.

A fact is `verified` iff it has at least one resolved citation into the same version *and* its value is
supported by the cited sentences:
- dates: the value's ISO date appears in the cited text in any common format
- amounts: the numeric value (with currency or percent) appears after normalizing separators and scale words
- names (party, governing law): case-insensitive token containment >= 0.8 (legal suffixes ignored)
- everything else: content-token overlap >= 0.5 after stopword removal

A value that does not parse as the field's type (a deadline written as "30 days", an amount written as words)
falls back to the token-overlap rule. Otherwise the fact is `unverified` with a reason code.
"""

import datetime as dt
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol

NO_CITATION = "NO_CITATION"
VALUE_NOT_IN_CITATION = "VALUE_NOT_IN_CITATION"

Status = Literal["verified", "unverified"]

DATE_FIELDS = frozenset({"effective_date", "expiry_date", "deadline"})
NAME_FIELDS = frozenset({"party", "governing_law"})
NAME_MIN_NUM, NAME_MIN_DEN = 4, 5  # containment >= 0.8, compared in integers
OVERLAP_MIN_NUM, OVERLAP_MIN_DEN = 1, 2  # overlap >= 0.5


class Cited(Protocol):
    @property
    def version_id(self) -> uuid.UUID: ...

    @property
    def cited_text(self) -> str: ...


@dataclass(frozen=True)
class Verdict:
    status: Status
    reason: str | None = None


VERIFIED = Verdict("verified")
UNVERIFIED = Verdict("unverified", VALUE_NOT_IN_CITATION)


# --- tokens -----------------------------------------------------------------------------------------------

# fmt: off
_STOPWORDS = frozenset([
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from", "has", "have", "had", "in", "is",
    "it", "its", "of", "on", "or", "shall", "should", "that", "the", "their", "this", "to", "was", "were",
    "will", "with", "may", "must", "not", "no", "any", "all", "each", "such", "than", "then", "there",
    "these", "those", "which", "who", "whom", "under", "upon", "into", "out", "per", "via", "within",
    "without",
])
_LEGAL_SUFFIXES = frozenset([
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "lp",
    "llp", "lllp", "gmbh", "sa", "ag", "nv", "bv", "the",
])
# fmt: on
_WORD = re.compile(r"[a-z0-9]+")


def _stem(t: str) -> str:
    """A light stem so `terminate`/`terminated` and `services`/`service` compare equal."""
    for suffix in ("ing", "ed", "es", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 4:
            t = t[: -len(suffix)]
            break
    return t[:-1] if t.endswith("e") and len(t) > 4 else t


def tokens(text: str) -> set[str]:
    return {_stem(t) for t in _WORD.findall(text.lower())}


def content_tokens(text: str) -> set[str]:
    return {_stem(t) for t in _WORD.findall(text.lower()) if t not in _STOPWORDS}


def name_supported(value: str, cited: str) -> bool:
    want = {_stem(t) for t in _WORD.findall(value.lower()) if t not in _LEGAL_SUFFIXES}
    if not want:
        return False
    have = tokens(cited)
    return len(want & have) * NAME_MIN_DEN >= len(want) * NAME_MIN_NUM


def overlap_supported(value: str, cited: str) -> bool:
    want = content_tokens(value)
    if not want:
        return False
    return len(want & content_tokens(cited)) * OVERLAP_MIN_DEN >= len(want)


# --- dates ------------------------------------------------------------------------------------------------

_MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for m in names
}
_MONTH = r"(?P<mon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_ORD = r"(?:st|nd|rd|th)?"
_DATE_PATTERNS = (
    re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b"),
    re.compile(r"\b" + _MONTH + r"\s+(?P<d>\d{1,2})" + _ORD + r"\s*,?\s*(?P<y>\d{4})\b", re.I),
    re.compile(
        r"\b(?P<d>\d{1,2})" + _ORD + r"\s+(?:day\s+of\s+)?" + _MONTH + r"\s*,?\s*(?P<y>\d{4})\b", re.I
    ),
    re.compile(r"\b(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{4})\b"),
)


_ISO_SHAPED = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b")


def _date(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


def dates_in(text: str) -> set[dt.date]:
    """Every full date written in `text` (ISO, `June 1, 2020`, `1 June 2020`, `1st day of June, 2020`,
    `06/01/2020`). A numeric `a/b/yyyy` with both parts <= 12 yields both the US and the day-first reading."""
    found: set[dt.date] = set()
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            g = m.groupdict()
            y = int(g["y"])
            if g.get("mon"):
                found.add(_date(y, _MONTHS[g["mon"].lower()], int(g["d"])) or dt.date.min)
            elif g.get("a"):
                a, b = int(g["a"]), int(g["b"])
                found.add(_date(y, a, b) or dt.date.min)
                found.add(_date(y, b, a) or dt.date.min)
            else:
                found.add(_date(y, int(g["m"]), int(g["d"])) or dt.date.min)
    found.discard(dt.date.min)
    return found


def date_supported(value: str, cited: str) -> bool | None:
    """True/False when the value is a date; None when it is not (the caller falls back to token overlap)."""
    want = dates_in(value)
    if not want:
        # An ISO-shaped value that is not a real date (2020-02-30) must not pass on loose token overlap.
        return False if _ISO_SHAPED.search(value) else None
    return bool(want & dates_in(cited))


# --- amounts ----------------------------------------------------------------------------------------------

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCY_CODES = frozenset({"usd", "eur", "gbp", "jpy", "cad", "aud", "chf", "cny", "inr", "sgd", "hkd"})
_CURRENCY_WORDS = {"dollars": "USD", "dollar": "USD", "euros": "EUR", "euro": "EUR", "pounds": "GBP"}
_SCALES = {
    "thousand": Decimal(10) ** 3,
    "k": Decimal(10) ** 3,
    "million": Decimal(10) ** 6,
    "mm": Decimal(10) ** 6,
    "m": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "b": Decimal(10) ** 9,
}
_AMOUNT = re.compile(
    r"(?P<pre>[$€£¥]|\b(?:usd|eur|gbp|jpy|cad|aud|chf|cny|inr|sgd|hkd)\b)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<scale>thousand|million|billion|mm|bn|k|m|b)\b)?"
    r"(?P<post>\s*%|\s*per\s*cent\b|\s*percent\b|\s+(?:usd|eur|gbp|jpy|cad|aud|chf|cny|inr|sgd|hkd)\b"
    r"|\s+(?:dollars?|euros?|pounds)\b)?",
    re.I,
)


@dataclass(frozen=True)
class Amount:
    number: Decimal
    currency: str | None
    percent: bool


def _currency(token: str | None) -> str | None:
    if not token:
        return None
    t = token.strip().lower()
    return _CURRENCY_SYMBOLS.get(t) or (t.upper() if t in _CURRENCY_CODES else _CURRENCY_WORDS.get(t))


def amounts_in(text: str) -> list[Amount]:
    out: list[Amount] = []
    for m in _AMOUNT.finditer(text):
        try:
            n = Decimal(m["num"].replace(",", ""))
        except InvalidOperation:
            continue
        scale = m["scale"]
        post = (m["post"] or "").strip().lower()
        percent = post.startswith(("%", "per", "percent")) and "dollar" not in post
        if scale:
            n *= _SCALES[scale.lower()]
        cur = _currency(m["pre"]) or (None if percent else _currency(post))
        out.append(Amount(n.normalize(), cur, percent))
    return out


def amount_supported(value: str, cited: str) -> bool | None:
    """True/False when the value carries a number; None when it does not (token-overlap fallback)."""
    want = amounts_in(value)
    if not want:
        return None
    have = amounts_in(cited)
    for w in want:
        for h in have:
            if w.number != h.number or w.percent != h.percent:
                continue
            if w.currency and h.currency and w.currency != h.currency:
                continue
            return True
    return False


# --- verdict ----------------------------------------------------------------------------------------------


def _supported(field: str, value: str, cited: str) -> bool:
    if field in DATE_FIELDS:
        ok = date_supported(value, cited)
        if ok is not None:
            return ok
    elif field == "amount":
        ok = amount_supported(value, cited)
        if ok is not None:
            return ok
    elif field in NAME_FIELDS:
        return name_supported(value, cited)
    return overlap_supported(value, cited)


def verify_fact(field: str, value: str, evidence: Sequence[Cited], version_id: uuid.UUID) -> Verdict:
    """Verdict for one fact. `evidence` are its resolved references; only those into `version_id` count."""
    texts = [e.cited_text for e in evidence if e.version_id == version_id]
    if not texts:
        return Verdict("unverified", NO_CITATION)
    if _supported(field, value, "\n".join(texts)):
        return VERIFIED
    return UNVERIFIED


def verify_tone(evidence: Sequence[Cited], version_id: uuid.UUID) -> Verdict:
    """A tone label has no literal value to find in text, so it is `verified` iff at least one resolved
    citation into `version_id` backs it; otherwise `unverified` with `NO_CITATION`."""
    if any(e.version_id == version_id for e in evidence):
        return VERIFIED
    return Verdict("unverified", NO_CITATION)
