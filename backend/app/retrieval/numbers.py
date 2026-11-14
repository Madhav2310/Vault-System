"""Number and unit variants for lexical queries (S22.03).

`to_tsvector('english')` splits "1,200" into the lexemes `1` and `200` and drops "$", "%", parentheses, so a
question that says "$1.2 billion" never reaches a table cell that says "1,200" (in millions). For every amount
in the query this module returns the equivalent written forms; the lexical builder turns each form into a
phrase tsquery and ORs them with the query's own lexemes. Pure functions, no I/O, no model.

Bounded: at most `MAX_PER_NUMBER` forms per number and `MAX_TOTAL` per query. Forms contain only digits,
`.`, `,`, `-`, spaces, a scale word or `FY`, and are always passed as bound parameters.

Rules
- A bare number with no separator, sign, currency or scale ("4.2", "12") has no variants: the query's own
  lexemes already match it. A bare integer of 1,000 or more gets its grouped/ungrouped counterpart.
- A 4-digit year is not an amount. It produces fiscal-year forms only when written "fiscal 2022",
  "fiscal year 2022", "FY2022" or "FY22".
- Identifiers such as "10-K", "4.2(b)" or "INV-2024-001" are never read as amounts: a number must not touch
  a letter or hyphen on either side (a scale suffix such as "3.4B" excepted).
- Ranges ("$5-10 million", "5 to 10 million") give variants for both ends, sharing the scale.
- Only ASCII digits count after NFKC folding (full-width digits fold; other scripts are ignored).
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

MAX_PER_NUMBER = 8
MAX_TOTAL = 24

_SCALES: dict[str, int] = {
    "thousand": 10**3,
    "k": 10**3,
    "million": 10**6,
    "mm": 10**6,
    "mn": 10**6,
    "m": 10**6,
    "billion": 10**9,
    "bn": 10**9,
    "b": 10**9,
    "trillion": 10**12,
    "tn": 10**12,
}
_SHORT_SCALES = {"k", "m", "b"}  # one letter: only when glued to the digits ("3.4B") or after a currency sign
_WORD = {10**3: "thousand", 10**6: "million", 10**9: "billion", 10**12: "trillion"}

_NUM = r"[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?"
_CUR = r"(?:[$€£¥]|USD|EUR|GBP)"
_SCALE = r"(?:thousand|million|billion|trillion|mm|mn|bn|tn|[kmb])"
_AMOUNT = re.compile(
    rf"(?<![\w.\-,])(?P<open>\()?(?P<neg>-)?(?P<cur>{_CUR}\s?)?(?P<n1>{_NUM})"
    rf"(?:\s?(?P<sep>-|\u2013|to)\s?(?P<cur2>{_CUR}\s?)?(?P<n2>{_NUM}))?"
    rf"(?P<sp>\s?)(?P<scale>{_SCALE}\b|%)?(?P<close>\))?(?![\w\-]|\.[0-9]|,[0-9])",
    re.IGNORECASE,
)
_FISCAL = re.compile(
    r"(?<![\w\-])(?:fiscal(?:\s+year)?|fy)\s?(?P<y>(?:19|20)[0-9]{2}|[0-9]{2})(?![\w\-])", re.IGNORECASE
)


def _plain(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _grouped(d: Decimal) -> str:
    whole, _, frac = _plain(d).partition(".")
    return f"{int(whole):,}" + ("." + frac if frac else "")


def _short(v: Decimal) -> bool:
    """At least 1 with at most three decimals (a figure a table would print)."""
    exp = v.normalize().as_tuple().exponent
    return v >= 1 and isinstance(exp, int) and -exp <= 3


def _forms(value: Decimal, scale: int | None, negative: bool) -> list[str]:
    """Ordered equivalent written forms of `value * scale`, most likely first."""
    total = value * (scale or 1)
    out: list[str] = []

    def add(s: str) -> None:
        if s not in out:
            out.append(s)

    if scale is None:
        add(_grouped(total))
        add(_plain(total))
        if negative:
            add("-" + _plain(total))
        return out
    if scale in _WORD:
        add(f"{_plain(value)} {_WORD[scale]}")
    for unit in (10**9, 10**6):  # the same amount in billions / millions ("1,200 million" is "1.2 billion")
        v = total / unit
        if _short(v):
            add(f"{_grouped(v)} {_WORD[unit]}")
            add(f"{_plain(v)} {_WORD[unit]}")
    for unit in (10**6, 10**3, 1):  # the bare figure of a table whose caption carries the unit
        v = total / unit
        if _short(v) and v >= 100:  # a bare "5" or "12" matches everything: not worth a term
            add(_grouped(v))
            add(_plain(v))
    return out


def _is_year(plain: str) -> bool:
    return len(plain) == 4 and plain.isdigit() and 1900 <= int(plain) <= 2100


def number_variants(query: str) -> list[list[str]]:
    """One list of equivalent written forms per amount found in `query` (empty when none).

    Each inner list has at most MAX_PER_NUMBER entries; the total across lists is at most MAX_TOTAL."""
    q = unicodedata.normalize("NFKC", query)
    groups: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    budget = MAX_TOTAL

    def push(forms: list[str]) -> None:
        nonlocal budget
        forms = forms[: min(MAX_PER_NUMBER, budget)]
        key = tuple(forms)
        if forms and key not in seen:
            seen.add(key)
            groups.append(forms)
            budget -= len(forms)

    for m in _FISCAL.finditer(q):
        y = m.group("y")
        full = y if len(y) == 4 else "20" + y
        push([f"FY{full[2:]}", f"FY{full}", f"fiscal {full}"])

    for m in _AMOUNT.finditer(q):
        if budget <= 0:
            break
        raw_scale = (m.group("scale") or "").lower()
        if raw_scale == "%":
            continue  # "%" is dropped by the parser; the bare number is already a lexeme
        has_cur = bool(m.group("cur"))
        scale: int | None = _SCALES.get(raw_scale) if raw_scale else None
        if raw_scale in _SHORT_SCALES and m.group("sp") and not has_cur:
            scale = None  # "5 m", "10 b" with a space and no currency: not an amount scale
        if m.group("sep") in ("-", "\u2013") and scale is None and not has_cur:
            continue  # "9001-2015", "2021-2022": a hyphenated pair is an id or a year span, not a range
        negative = bool(m.group("neg")) or (bool(m.group("open")) and bool(m.group("close")))
        for text in (m.group("n1"), m.group("n2")):
            if not text:
                continue
            plain = text.replace(",", "")
            bare = scale is None and not has_cur and "," not in text and not negative
            if bare and ("." in plain or len(plain) < 4 or _is_year(plain)):
                continue  # nothing to add: the query's own lexemes already match it
            try:
                value = Decimal(plain)
            except InvalidOperation:  # pragma: no cover - the regex only admits digits
                continue
            push(_forms(value, scale, negative))
    return groups


def flat_variants(query: str) -> list[str]:
    """All variant forms of `query`, flattened and de-duplicated, order kept."""
    return list(dict.fromkeys(f for g in number_variants(query) for f in g))
