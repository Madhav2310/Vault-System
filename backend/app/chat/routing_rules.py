"""Fast-path routing rules (design §8.2): when `analyze_query` can be skipped. Pure, data-driven.

A first-turn question without pronouns, comparison or meta cues in a scope of at most one document needs no
rewrite or intent label: the raw question is the standalone query and the route is `qa`. Any doubt means
"analyze" (a false skip costs an answer, a false analyze costs about two seconds).
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


@dataclass(frozen=True)
class FastPathDecision:
    skip_analyze: bool
    why: str  # FAST_PATH, or the rule that blocked the skip (history, length, pronoun, comparison, meta)


def fast_path(question: str, *, prior_turns: int, scope_docs: int) -> FastPathDecision:
    """Decide whether `analyze_query` can be skipped. Deterministic; never raises."""
    q = " ".join(question.split())
    if prior_turns > 0:
        return FastPathDecision(False, "history")
    if not q or len(q) > MAX_FAST_QUESTION_CHARS:
        return FastPathDecision(False, "length")
    if _PRONOUN.search(q):
        return FastPathDecision(False, "pronoun")
    if scope_docs > 1 and _COMPARE.search(q):
        return FastPathDecision(False, "comparison")
    if _META.search(q):
        return FastPathDecision(False, "meta")
    return FastPathDecision(True, FAST_PATH)
