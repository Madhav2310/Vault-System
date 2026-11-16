"""Rule-based sentence segmenter.

Returns half-open (start, end) offsets into the unmodified input, so citation handles can slice
`original_text` exactly. A blank line always ends a sentence; a single newline
(a PDF line wrap) does not by itself. Table text: every non-empty line is one sentence.
"""

import re
from typing import Literal


def _words(s: str) -> frozenset[str]:
    return frozenset(s.split())


# Never ends a sentence, whatever follows (titles, references, citation and date abbreviations).
_HARD = _words(
    "Mr Mrs Ms Dr Prof Messrs Mt St Gen Gov Sen Rep Hon Rev Lt Col Capt Sgt "
    "No Nos Sec Secs Fig Figs Eq Eqs Art Arts Vol Vols Ch Cl Para Paras Ex Exh Ref App Appx Dept "
    "pp p vs v cf approx est Cir Ct Dist Supp Stat Cal "
    "Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec "
    "e.g i.e"
)
# Ends a sentence only when the next word is a typical sentence opener (or a list enumerator).
_SOFT = _words("Inc Corp Ltd Co LLC LLP LP PLC Jr Sr etc al U.S U.K L.P N.A S.A a.m p.m A.M P.M Ph.D")
_STARTERS = _words(
    "The This These Those That Such It Its In On At As Any Each For If We Our They Their He She "
    "However Following Upon Under All Not Pursuant Subject Notwithstanding An Both During After Before "
    "By To With Without There Where When Since Although Because Accordingly Additionally Further "
    "Moreover Finally See Neither Either No Except Unless Prior"
)

# Full-width and non-Latin sentence terminators end a sentence without needing a following space or capital.
_UNICODE_TERM = "".join(chr(c) for c in (0x3002, 0xFF01, 0xFF1F, 0x0964, 0x061F, 0x06D4))
_TERM = re.compile("[.!?" + chr(0x2026) + _UNICODE_TERM + "]+")
_WORD_WINDOW = 48  # how far back to look for the word before a period (and the longest clause label)
# Typographic quotes by code point (keeps the source ASCII).
_CLOSERS = frozenset("\"')]}*") | {chr(c) for c in (0x201D, 0x2019, 0xBB)}
_OPENERS = frozenset("\"'([{") | {chr(c) for c in (0x201C, 0x2018, 0xAB)}
_ENUM = re.compile(r"\((?:[a-z]|[ivx]{1,4}|\d{1,2})\)")
_WORD_BEFORE = re.compile(r"[A-Za-z][A-Za-z.]*$")
_NEXT_WORD = re.compile(r"[A-Za-z]+")
_PARA_BREAK = re.compile(r"(?:\n[ \t\r]*){2,}")
_LINE = re.compile(r"[^\n]+")
# "Section 3.1", "Item 1A", "4.2(b)", "(a)", "iv", "A": a clause label at the start of a sentence.
_ROMAN = r"(?:i{1,3}|iv|vi{0,3}|ix|x{1,3}|I{1,3}|IV|VI{0,3}|IX|X{1,3})"
_LABEL = re.compile(
    r"(?:(?:Section|Sec|Article|Art|Item|Part|Clause|Paragraph|Exhibit|Schedule|Appendix|Chapter|Rule"
    r"|Annex|Note|Table|Figure)\.?\s+)?"
    rf"(?:\d+(?:\.\d+)*[A-Za-z]?|[A-Za-z]|{_ROMAN})(?:\([A-Za-z0-9]+\))*"
)


def _trim(text: str, lo: int, hi: int) -> tuple[int, int] | None:
    while lo < hi and text[lo].isspace():
        lo += 1
    while hi > lo and text[hi - 1].isspace():
        hi -= 1
    return (lo, hi) if lo < hi else None


def _is_break(text: str, start: int, m: re.Match[str], after_ws: int, content: int) -> bool:
    """Whether the terminator run `m` ends a sentence. `after_ws` is the next non-space index,
    `content` the index after any opening quote/bracket."""
    nxt = text[content]
    enum = _ENUM.match(text, after_ws) is not None
    if m.group()[-1] in _UNICODE_TERM:
        return True
    if not (nxt.isupper() or nxt.isdigit() or nxt in "$" + chr(0xA7) or enum):
        return False
    run = m.group()
    if run != ".":
        return True  # "?", "!", ellipsis
    # Only a bounded window is inspected: copying `text[start:m.start()]` for every period would make
    # long runs of "Dr." / "No." quadratic.
    if m.start() - start <= _WORD_WINDOW and _LABEL.fullmatch(text[start : m.start()].strip()):
        return False
    prior = text[max(start, m.start() - _WORD_WINDOW) : m.start()].strip()
    found = _WORD_BEFORE.search(prior)
    word = found.group() if found else ""
    if word in _HARD:
        return False
    if word in _SOFT or (len(word) == 1 and word.isupper()):
        following = _NEXT_WORD.match(text, content)
        return enum or (following is not None and following.group() in _STARTERS)
    return True


def _split_paragraph(text: str, lo: int, hi: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = lo
    for m in _TERM.finditer(text, lo, hi):
        end = m.end()
        while end < hi and text[end] in _CLOSERS:
            end += 1
        if end >= hi:
            break
        if m.group()[-1] in _UNICODE_TERM and not text[end].isspace():
            # CJK and similar scripts do not put a space after the terminator: it ends the sentence
            if end > start:
                spans.append((start, end))
                start = end
            continue
        if not text[end].isspace():
            continue
        after_ws = end
        while after_ws < hi and text[after_ws].isspace():
            after_ws += 1
        content = after_ws
        while content < hi and text[content] in _OPENERS:
            content += 1
        if content >= hi:
            break
        if _is_break(text, start, m, after_ws, content):
            spans.append((start, end))
            start = after_ws
    spans.append((start, hi))
    return spans


def split_sentences(text: str, *, kind: Literal["text", "table"] = "text") -> list[tuple[int, int]]:
    """Sentence spans as half-open offsets into `text`; gaps between spans are whitespace only."""
    out: list[tuple[int, int]] = []
    if kind == "table":
        for line in _LINE.finditer(text):
            trimmed = _trim(text, line.start(), line.end())
            if trimmed:
                out.append(trimmed)
        return out
    pos = 0
    for brk in [*_PARA_BREAK.finditer(text), None]:
        para_end = brk.start() if brk else len(text)
        trimmed = _trim(text, pos, para_end)
        if trimmed:
            out.extend(_split_paragraph(text, *trimmed))
        if brk:
            pos = brk.end()
    return out
