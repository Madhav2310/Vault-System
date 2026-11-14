"""Fast-path routing rules (design §8.2): when `analyze_query` can be skipped. Pure, data-driven.

A question without pronouns, comparison or meta cues needs no rewrite or intent label: the raw question is the
standalone query and the route is `qa`. A follow-up (prior turns) qualifies only when it also reads as
self-contained: at least `MIN_FOLLOWUP_WORDS` words, no ellipsis cue ("and", "what about", "the rest", ...)
and a concrete anchor (a quoted term, a capitalised name or a document noun). Any doubt means "analyze"
(a false skip costs an answer, a false analyze costs about two seconds).
"""

import re
from dataclasses import dataclass

FAST_PATH = "FAST_PATH"
MAX_FAST_QUESTION_CHARS = 500  # longer questions are capped by the analyzer; let it handle them

# Whole words, case-insensitive; multi-word entries match as phrases.
PRONOUN_CUES = (
    "it", "its", "that", "this", "these", "those", "they", "them", "their", "he", "she", "him", "her", "his",
    "there", "the above", "the former", "the latter", "the same", "the other", "previous", "earlier", "again",
)  # fmt: skip
COMPARISON_CUES = (
    "compare", "comparison", "difference", "differences", "differ", "differs", "versus", "vs", "both", "each",
    "across", "between", "contrast",
)  # fmt: skip
ELLIPSIS_OPENERS = (
    "and", "also", "but", "so", "then", "why", "how come", "what about", "how about", "ok", "okay", "thanks",
    "additionally", "furthermore", "now",
)  # fmt: skip
ELLIPSIS_CUES = (
    "what about", "how about", "else", "another", "more", "other", "others", "same", "such", "instead",
    "rest", "remaining", "next", "first", "second", "third", "last", "one", "ones", "either", "neither",
    "former",
)  # fmt: skip
DOCUMENT_NOUNS = (
    "agreement", "contract", "document", "lease", "invoice", "filing", "report", "policy", "statement",
    "clause", "section", "article", "exhibit", "appendix", "schedule", "addendum", "amendment", "page",
    "paragraph", "memo", "letter",
)  # fmt: skip
MIN_FOLLOWUP_WORDS = 5
META_CUES = (
    "how many documents", "list my", "which documents", "what documents", "my documents", "my files",
    "all documents", "what can you", "who are you",
)  # fmt: skip


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alt = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.IGNORECASE)


_PRONOUN = _pattern(PRONOUN_CUES)
_COMPARE = _pattern(COMPARISON_CUES)
_META = _pattern(META_CUES)
_ELLIPSIS = _pattern(ELLIPSIS_CUES)
_OPENER = re.compile(
    r"^\W*(?:" + "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in ELLIPSIS_OPENERS) + r")(?!\w)", re.I
)
_DOC_NOUN = _pattern(DOCUMENT_NOUNS + tuple(n + "s" for n in DOCUMENT_NOUNS))
_QUOTED = re.compile(r'["“”][^"“”]{2,}["“”]')


@dataclass(frozen=True)
class FastPathDecision:
    skip_analyze: bool
    why: str  # FAST_PATH, or the rule that blocked the skip (history, length, pronoun, comparison, meta)


def fast_path(question: str, *, prior_turns: int, scope_docs: int) -> FastPathDecision:
    """Decide whether `analyze_query` can be skipped. Deterministic; never raises."""
    q = " ".join(question.split())
    if not q or len(q) > MAX_FAST_QUESTION_CHARS:
        return FastPathDecision(False, "length")
    if _PRONOUN.search(q):
        return FastPathDecision(False, "pronoun")
    if scope_docs > 1 and _COMPARE.search(q):
        return FastPathDecision(False, "comparison")
    if _META.search(q):
        return FastPathDecision(False, "meta")
    if prior_turns > 0:
        if _OPENER.search(q) or _ELLIPSIS.search(q):
            return FastPathDecision(False, "ellipsis")
        if len(q.split()) < MIN_FOLLOWUP_WORDS or not _has_anchor(q):
            return FastPathDecision(False, "history")
    return FastPathDecision(True, FAST_PATH)


def _has_anchor(q: str) -> bool:
    """A quoted term, a document noun, or a capitalised word that does not open the sentence."""
    if _QUOTED.search(q) or _DOC_NOUN.search(q):
        return True
    return any(len(w.strip("?.,;:!()")) >= 2 and w[0].isupper() for w in q.split()[1:])
