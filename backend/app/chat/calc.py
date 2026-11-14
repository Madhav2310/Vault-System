"""Typed calculation segments (design §8.5, S22.08).

With `chat_calc` on, a segment may carry `calc: {op, operands: [{handle, value_text}], unit, stated_result}`.
The model only chooses the operation and which cited sentences hold the operands. Code reads every operand
number from the cited sentence itself (the operand's `value_text` must occur there), tracks currency, percent
and unit scale (thousand/million/billion, also from an "in millions" header in the same sentence), computes
with `Decimal`, rounds half-up at the precision the stated result implies, and writes the segment text from a
template. The model's `stated_result` is only compared: a different code value wins (`CALC_CORRECTED`). An
operand that is not found in its cited sentence, or an uncomputable operation (division by zero, wrong operand
count, more than `MAX_OPERANDS`, percent mixed with amounts), drops the segment to plain, uncited text of the
model's own wording (`CALC_OPERAND_UNVERIFIED`): a computed number is never shown as verified unless code
verified it. A malformed `calc` degrades to a normal segment. No model call is involved.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from typing import Any

from app.chat.citations import Resolution, ResolvedSegment, resolve, resolve_segment
from app.chat.evidence import CALC_OPS, EvidenceSet
from app.explain.templates import calc_text

CALC_CORRECTED = "CALC_CORRECTED"
CALC_OPERAND_UNVERIFIED = "CALC_OPERAND_UNVERIFIED"
CALC_PROMPT: tuple[str, int] = ("answer_rag", 5)
MAX_OPERANDS = 50
MAX_VALUE_CHARS = 40
MAX_DP = 6

_SCALE_WORDS = {
    "k": 10**3, "thousand": 10**3, "m": 10**6, "mm": 10**6, "million": 10**6,
    "b": 10**9, "bn": 10**9, "billion": 10**9, "trillion": 10**12,
}  # fmt: skip
_WORD_OF = {10**3: "thousand", 10**6: "million", 10**9: "billion", 10**12: "trillion"}
_MINUS = "-\N{MINUS SIGN}\N{EN DASH}"
_NUMBER = re.compile(
    rf"^(?P<open>\()?\s*(?P<s1>[{_MINUS}+])?\s*(?P<cur>[$€£¥])?\s*(?P<s2>[{_MINUS}])?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*"
    r"(?P<suf>%|percent|thousand|million|billion|trillion|mm|bn|[kKmMbB])?\s*(?P<close>\))?$",
    re.IGNORECASE,
)
_AFTER = re.compile(
    r"^\s*(?P<pct>%|percent\b|per cent\b)|^\s*(?P<word>thousand|million|billion|trillion)\b", re.IGNORECASE
)
_HEADER = re.compile(r"\bin\s+(?P<w>thousands|millions|billions)\b", re.IGNORECASE)
_HEADER_SCALE = {"thousands": 10**3, "millions": 10**6, "billions": 10**9}
_CURRENCY_BEFORE = re.compile(r"([$€£¥])\s*$")


class Unverified(Exception):
    """An operand is not in its cited sentence, or the arithmetic is not defined."""


@dataclass(frozen=True)
class Operand:
    value: Decimal  # base units (scale applied; a percent operand keeps the percent number)
    pct: bool
    symbol: str
    scale: int
    decimals: int


@dataclass(frozen=True)
class Outcome:
    text: str
    handles: tuple[str, ...]
    reasons: tuple[str, ...] = ()


def _split(value_text: str) -> tuple[Decimal, bool, str, int, int, bool] | None:
    """One number as written: (magnitude, pct, symbol, explicit scale or 0, decimals, negative)."""
    m = _NUMBER.match(value_text.strip())
    if m is None or bool(m["open"]) != bool(m["close"]):
        return None
    digits = m["num"].replace(",", "")
    try:
        v = Decimal(digits)
    except InvalidOperation:
        return None
    suf = (m["suf"] or "").lower()
    pct = suf in ("%", "percent")
    scale = 0 if pct or not suf else _SCALE_WORDS[suf]
    neg = bool(m["open"]) or bool(m["s1"] and m["s1"] in _MINUS) or bool(m["s2"])
    return v, pct, m["cur"] or "", scale, len(digits.split(".")[1]) if "." in digits else 0, neg


def _occurrences(sentence: str, value_text: str) -> list[int]:
    """Offsets where `value_text` occurs as a whole number (not inside a longer number or word)."""
    out: list[int] = []
    low, needle, start = sentence.casefold(), value_text.casefold(), 0
    while (i := low.find(needle, start)) != -1:
        j = i + len(needle)
        b1 = sentence[i - 1] if i else " "
        b2 = sentence[i - 2] if i > 1 else " "
        a1 = sentence[j] if j < len(sentence) else " "
        a2 = sentence[j + 1] if j + 1 < len(sentence) else " "
        starts_num, ends_num = needle[0].isdigit() or needle[0] == ".", needle[-1].isdigit()
        glued_before = starts_num and (b1.isdigit() or (b1 in ",." and b2.isdigit()))
        glued_after = ends_num and (a1.isdigit() or a1.isalpha() or (a1 in ",." and a2.isdigit()))
        if not (glued_before or glued_after):
            out.append(i)
        start = i + 1
    return out


def read_operand(sentence: str, value_text: str) -> Operand | None:
    """The operand as the cited sentence states it, or None when `value_text` is not in the sentence."""
    sentence = " ".join(sentence.split())
    value_text = " ".join(value_text.split())
    if not value_text or len(value_text) > MAX_VALUE_CHARS:
        return None
    parsed = _split(value_text)
    occ = _occurrences(sentence, value_text)
    if parsed is None or not occ:
        return None
    v, pct, symbol, scale, decimals, neg = parsed
    before, after = sentence[: occ[0]], sentence[occ[0] + len(value_text) :]
    if not symbol and (cm := _CURRENCY_BEFORE.search(before)):
        symbol = cm.group(1)
    if not pct and not scale and (am := _AFTER.match(after)):
        if am["pct"]:
            pct = True
        else:
            scale = _SCALE_WORDS[am["word"].lower()]
    if not neg and before.rstrip().endswith("(") and after.lstrip().startswith(")"):
        neg = True
    if not pct and not scale and (hm := _HEADER.search(sentence)):
        scale = _HEADER_SCALE[hm["w"].lower()]
    scale = scale or 1
    return Operand(-v * scale if neg else v * scale, pct, symbol, scale, decimals)


def _quant(d: Decimal, dp: int) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-dp), rounding=ROUND_HALF_UP)


def _compute(op: str, xs: Sequence[Operand]) -> tuple[Decimal, str] | None:
    """(value, kind): `amount` (shown in the operands' scale), `pct` or `ratio`; None when uncomputable."""
    vals = [x.value for x in xs]
    if len({x.pct for x in xs}) != 1:
        return None
    if op in ("ratio", "percent_change"):
        if len(xs) != 2:
            return None
        a, b = vals
        if (a if op == "percent_change" else b) == 0:
            return None
        return ((b - a) / abs(a) * 100, "pct") if op == "percent_change" else (a / b, "ratio")
    if (op in ("add", "subtract") and len(xs) < 2) or (op == "subtract" and len(xs) != 2):
        return None
    kind = "pct" if xs[0].pct else "amount"
    if op == "subtract":
        return vals[0] - vals[1], kind
    total = sum(vals, Decimal(0))
    return (total / len(vals) if op == "average" else total), kind


def _fmt(d: Decimal, dp: int, kind: str, scale: int, symbol: str, unit: str) -> str:
    q = _quant(d, dp)
    body = f"{abs(q):,.{dp}f}"
    sign = "-" if q < 0 else ""
    if kind == "pct":
        return f"{sign}{body}%"
    if kind == "ratio":
        return f"{sign}{body}"
    word = f" {_WORD_OF[scale]}" if scale > 1 else ""
    return f"{sign}{symbol}{body}{word}" + (f" {unit}" if unit and not symbol else "")


def _clean_unit(unit: Any) -> str:
    return re.sub(r"[^A-Za-z /]", "", unit)[:16].strip() if isinstance(unit, str) else ""


def calculate(calc: Any, evidence: EvidenceSet) -> tuple[str, tuple[str, ...], bool] | None:
    """(text, operand handles, corrected) for a verified calculation; None when `calc` is malformed.
    Raises `Unverified` when an operand or the arithmetic cannot be verified."""
    if not isinstance(calc, dict):
        return None
    op, operands = calc.get("op"), calc.get("operands")
    if op not in CALC_OPS or not isinstance(operands, list) or not operands:
        return None
    for o in operands:
        if not (
            isinstance(o, dict) and isinstance(o.get("handle"), str) and isinstance(o.get("value_text"), str)
        ):
            return None
    if len(operands) > MAX_OPERANDS:
        raise Unverified
    xs: list[Operand] = []
    for o in operands:
        entry = evidence.manifest.get(o["handle"])
        if entry is None:
            raise Unverified
        x = read_operand(
            evidence.chunks[entry.chunk_id].original_text[entry.start : entry.end], o["value_text"]
        )
        if x is None:
            raise Unverified
        xs.append(x)
    with localcontext() as ctx:
        ctx.prec = 60
        res = _compute(op, xs)
        if res is None:
            raise Unverified
        value, kind = res
        scale = max(x.scale for x in xs) if kind == "amount" else 1
        symbol = next((x.symbol for x in xs if x.symbol), "") if kind == "amount" else ""
        shown = value / scale
        stated = _split(str(calc.get("stated_result") or ""))
        if stated is not None:
            sv, _, _, sscale, sdp, sneg = stated
            sv = -sv if sneg else sv
            dp = min(sdp, MAX_DP)
            stated_shown: Decimal | None = sv * sscale / scale if sscale else sv
        else:
            dp = min({"pct": 1, "ratio": 2}.get(kind, max(x.decimals for x in xs)), MAX_DP)
            stated_shown = None
        corrected = stated_shown is not None and _quant(stated_shown, dp) != _quant(shown, dp)
        unit = _clean_unit(calc.get("unit")) if kind == "amount" else ""
        result = _fmt(shown, dp, kind, scale, symbol, unit)
    texts = [" ".join(o["value_text"].split()) for o in operands]
    return calc_text(op, texts, result), tuple(dict.fromkeys(o["handle"] for o in operands)), corrected


def _field(seg: Any, name: str) -> Any:
    v: Any = seg.get(name) if isinstance(seg, dict) else getattr(seg, name, None)
    return v.model_dump() if hasattr(v, "model_dump") else v


def _apply(seg: Any, evidence: EvidenceSet) -> Outcome:
    text, handles = _field(seg, "text"), list(_field(seg, "evidence") or [])
    plain = Outcome(text, tuple(handles))
    calc = _field(seg, "calc")
    if calc is None:
        return plain
    try:
        done = calculate(calc, evidence)
    except Unverified:
        return Outcome(text, (), (CALC_OPERAND_UNVERIFIED,))  # uncited: never shown as verified
    except Exception:  # a malformed calculation never fails the answer
        return plain
    if done is None:
        return plain
    out, operand_handles, corrected = done
    handles = list(dict.fromkeys([*handles, *operand_handles]))
    return Outcome(out, tuple(handles), (CALC_CORRECTED,) if corrected else ())


def resolve_calc_segment(n: int, seg: Any, evidence: EvidenceSet) -> tuple[ResolvedSegment, tuple[str, ...]]:
    """One segment with its calculation (if any) checked by code; no metric side effect (streaming path)."""
    o = _apply(seg, evidence)
    return resolve_segment(n, {"text": o.text, "evidence": list(o.handles)}, evidence), o.reasons


def resolve_calc(segments: Sequence[Any], evidence: EvidenceSet) -> Resolution:
    """`citations.resolve` for segments that may carry a calculation; reasons are merged in order."""
    outs = [_apply(s, evidence) for s in segments]
    res = resolve([{"text": o.text, "evidence": list(o.handles)} for o in outs], evidence)
    reasons = list(dict.fromkeys([*res.reasons, *(r for o in outs for r in o.reasons)]))
    return Resolution(res.segments, tuple(reasons), res.unresolved_count)


def calc_call(state: Any, settings: Any, full: bool) -> dict[str, Any]:
    """`generate` keyword arguments of a calculation turn; empty (nothing changes) unless `chat_calc` is on
    and the turn is plain RAG (not compare, not whole-document)."""
    if not getattr(settings, "chat_calc", False) or full or state.compare:
        return {}
    return {"prompt": CALC_PROMPT, "calc": True}
