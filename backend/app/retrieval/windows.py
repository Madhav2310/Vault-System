"""Query-focused rerank windows (plan S16.03).

A cross-encoder's cost grows faster than linearly with passage length, and a chunk is ~400 tokens. The rerank
passage is the best-matching window of whole sentences (<= `max_words`), so the reranker sees the part of the
chunk that answers the question. Windows are used for reranking only; they are never evidence (invariant I1).
"""

import re
from collections.abc import Sequence

DEFAULT_WINDOW_WORDS = 180
_WORD = re.compile(r"[a-z0-9]+")
_STOP = (
    "a an and are as at be by for from has have how i in is it its of on or that the this to was were "
    "what when where which who whom why will with does do did can could should would may might shall not no"
)
STOPWORDS = frozenset(_STOP.split())


def stems(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall(text.lower()):
        if w in STOPWORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)
    return out


def _words(s: str) -> int:
    return len(s.split())


def _cap(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    cut = s.rfind(" ", 0, max_chars)
    return s[: cut if cut > 0 else max_chars]


def focus_window(
    text: str,
    sentences: Sequence[tuple[int, int]],
    query: str,
    max_words: int = DEFAULT_WINDOW_WORDS,
    max_chars: int = 1050,
) -> str:
    """The contiguous run of whole sentences (<= max_words) with the most distinct query stems.

    Ties go to the earliest window; no overlap -> the first `max_words` words; one over-long sentence is cut
    at the word limit. The result is also capped at `max_chars` (word boundary): the reranker input limit."""
    if _words(text) <= max_words and len(text) <= max_chars:
        return text
    q = stems(query)
    spans = [(a, b) for a, b in sentences if b > a] or [(0, len(text))]
    counts = [_words(text[a:b]) for a, b in spans]
    hits = [len(q & stems(text[a:b])) for a, b in spans]
    best: tuple[int, int, int] | None = None  # (score, -start, end) maximised; ties -> earliest start
    for i in range(len(spans)):
        words = 0
        chars = 0
        covered: set[str] = set()
        for j in range(i, len(spans)):
            words += counts[j]
            chars += spans[j][1] - spans[j][0] + 1
            if (words > max_words or chars > max_chars) and j > i:
                break
            covered |= q & stems(text[spans[j][0] : spans[j][1]])
            score = len(covered)
            if best is None or (score, -i) > (best[0], best[1]):
                best = (score, -i, j)
    assert best is not None
    score, neg_i, j = best
    i = -neg_i
    if score == 0 or not any(hits):
        return _cap(" ".join(text.split()[:max_words]), max_chars)
    window = text[spans[i][0] : spans[j][1]]
    out = " ".join(window.split()[:max_words]) if _words(window) > max_words else window
    return _cap(out, max_chars)
