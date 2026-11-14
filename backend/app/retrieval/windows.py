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


_LEADER = re.compile(r"[ \t]*(?:\.{4,}|(?:\.[ \t]){3,}\.?|…{2,})[ \t]*")
LEADER_MARKER = " … "
_LETTERS = re.compile(r"[A-Za-z]{2}")


def collapse_leaders(text: str, sentences: Sequence[tuple[int, int]]) -> tuple[str, list[tuple[int, int]]]:
    """Replace each dot-leader run (4+ dots, ". . . ." sequences, repeated ellipses) with one short marker and
    return the new text with the sentence spans remapped to it.

    Reranker copy only: evidence, citations and `original_text` are never touched (I1)."""
    pieces: list[str] = []
    runs: list[tuple[int, int, int]] = []  # (old start, old end, new start) of each replaced run
    pos = new_pos = 0
    for m in _LEADER.finditer(text):
        pieces.append(text[pos : m.start()])
        new_pos += m.start() - pos
        runs.append((m.start(), m.end(), new_pos))
        pieces.append(LEADER_MARKER)
        new_pos += len(LEADER_MARKER)
        pos = m.end()
    pieces.append(text[pos:])
    new_text = "".join(pieces)

    def remap(off: int, is_end: bool) -> int:
        shift = 0
        for old_start, old_end, new_start in runs:
            if off <= old_start:
                break
            if off < old_end:  # inside a run: land on the marker's ellipsis
                return new_start + (2 if is_end else 1)
            shift = new_start + len(LEADER_MARKER) - old_end
        return min(off + shift, len(new_text))

    return new_text, [(remap(a, False), remap(b, True)) for a, b in sentences]


def focus_window(
    text: str,
    sentences: Sequence[tuple[int, int]],
    query: str,
    max_words: int = DEFAULT_WINDOW_WORDS,
    max_chars: int = 1050,
    collapse: bool = False,
) -> str:
    """The contiguous run of whole sentences (<= max_words) with the most distinct query stems.

    Ties go to the earliest window; no overlap -> the first `max_words` words; one over-long sentence is cut
    at the word limit. The result is also capped at `max_chars` (word boundary): the reranker input limit.

    `collapse` first collapses dot leaders (see `collapse_leaders`) so the character budget goes to labels and
    numbers, and, only when a leader was collapsed, the window then also takes the number-only sentences that
    follow its last sentence (the row's values: they add no query stem, so the stem count alone never reaches
    them); a chunk without leaders, or `collapse` off, gets the unchanged window.
    Reranker input only, never evidence (I1)."""
    collapsed = False
    if collapse:
        new_text, new_sentences = collapse_leaders(text, sentences)
        collapsed = new_text != text  # a chunk without leaders keeps exactly the uncollapsed window
        text, sentences = new_text, new_sentences
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
    while collapsed and j + 1 < len(spans) and not _LETTERS.search(text[spans[j + 1][0] : spans[j + 1][1]]):
        grown = text[spans[i][0] : spans[j + 1][1]]  # numeric-only sentences are the cells of the last row
        if _words(grown) > max_words or len(grown) > max_chars:
            break
        j += 1
    if score == 0 or not any(hits):
        return _cap(" ".join(text.split()[:max_words]), max_chars)
    window = text[spans[i][0] : spans[j][1]]
    out = " ".join(window.split()[:max_words]) if _words(window) > max_words else window
    return _cap(out, max_chars)
