"""Deterministic explanation templates (design §10): one per reason code, rendered from run facts.

The model is never asked to explain itself. Every reason code a turn can record has a template here (a unit
test greps the constants in `app/chat`, `app/retrieval` and `app/llm`); an empty template means the code is
recorded for operators and adds no line for users. Wording never names a model, a prompt or an internal
service, and quotes no document text.
"""

from typing import Any

# ruff: noqa: E501  (user-facing prose is kept one message per line)

ANSWERED_ONE_DOC = "Answered from {passages} in '{title}' v{version_no}."
ANSWERED_MANY_DOCS = "Answered from {passages} across {documents}: {titles}."
FULL_CONTEXT_READ = "Read the whole document ({documents}, {tokens})."
FULL_CONTEXT_READ_MANY = "Read the documents in full ({documents}, {tokens})."
UNSUPPORTED_ONE = "One sentence is not backed up by its citation, so read it with care."
UNSUPPORTED_MANY = "{count} sentences are not backed up by their citations, so read them with care."
NO_ANSWER = "No answer was written this time. Try asking again."

# Code-written responses (S23.01, docs/voice.md). Always on: they are deterministic, the model writes none of them.
NO_DOCUMENTS_TEXT = (
    "There are no ready documents to search yet. Upload one, or give processing a moment to finish."
)
NO_EVIDENCE_TEXT = (
    "I could not find this in your documents. "
    "Try asking in different words, or name the document or section you have in mind."
)
ABSTAIN_TEXT_ONE = (
    "I could not find this in your 1 document. "
    "Try asking in different words, or name the section you have in mind."
)
ABSTAIN_TEXT_MANY = (
    "I could not find this in your {n} documents. "
    "Try asking in different words, or name the document you have in mind."
)
NOT_FOUND_ELEMENT = (
    "I did not find anything about {element} in these documents. Try a document that covers it."
)
PARTIAL_ANSWER_TEXT = (
    "That is what your documents back up. I could not find the rest, so please check the document itself "
    "or name the section you have in mind."
)
SIDE_NOT_FOUND = "I did not find anything about this in {title}. Try a document that covers it."
CLARIFY_TEXT = "More than one of your documents could answer this. Which one should I look in?"
UNSUPPORTED_ABSTAIN_TEXT = (
    "I could not write an answer that your documents clearly back up. "
    "Try asking in different words, or name the document or section you have in mind."
)
ERROR_TEXT = "That did not work on my side, and no answer was written. Please try again in a moment."
QUOTA_REACHED = "You have reached today's usage limit. It resets in {retry} s (at midnight UTC), so you can ask again then."
RATE_LIMITED = "That was a lot of requests at once. Please try again in {retry} s."
STALE_ANSWER = (
    "A newer version of this document was added after this answer. Ask again to see if anything changed."
)

# reason code -> user-facing line. Keys are the code strings recorded in `rag_runs.reason_codes`.
REASON_TEMPLATES: dict[str, str] = {
    "NO_EVIDENCE": "Nothing in your documents matched closely enough. Try different words or name the document.",
    "ABSTAIN_NO_EVIDENCE": (
        "The closest passages were too weak to answer from, so none was written. "
        "Try different words or name the document."
    ),
    "ABSTAIN_SCORED": (
        "The passages found were too weak to answer from, so none was written. "
        "Try different words or name the document."
    ),
    "PARTIAL_ANSWER": (
        "Only part of the question was backed up by your documents, and the answer says so. "
        "Try naming the document or section for the rest."
    ),
    "SCOPE_EMPTY": "There are no ready documents in scope. Upload one, or wait for processing to finish.",
    "FULL_CONTEXT_UNAVAILABLE": (
        "Reading the whole document was not possible this time, so the best-matching passages were used."
    ),
    "CLARIFY_AMBIGUOUS_DOCUMENT": "The question could point to more than one document, so you were asked which.",
    "CLARIFY_AMBIGUOUS_QUERY": "The question could be read more than one way, so you were asked to clarify.",
    "CLARIFY_NO_ANTECEDENT": "The question did not say which document it meant, so you were asked which.",
    "CLARIFY_REJECTED": "A clarifying question was not needed, so the question was answered as written.",
    "TARGET_OUT_OF_SCOPE": "A document named in the question is outside this chat's scope, so it was left out.",
    "TARGET_IGNORED": "A document named in the question could not be used, so it was left out.",
    "ANALYZE_FALLBACK": "The question was used exactly as written, because it could not be reworded for search.",
    "CITATION_UNRESOLVED": "Some citations could not be matched to your documents and were left out.",
    "STOP_REFUSAL": "No answer could be written for this request. Try asking in a different way.",
    "STOP_MAX_TOKENS": "The answer reached its length limit, so only the complete sentences are shown.",
    "GENERATION_INVALID": "The answer came back in a form that could not be used, so none is shown. Try asking again.",
    "TURN_ERROR": "Something went wrong on our side, so no answer was written. Try asking again.",
    "FULL_CONTEXT_TOO_LARGE": "The documents were too long to read whole, so the best passages were searched instead.",
    "SUPPORT_CHECK_FAILED": "The citations could not be double-checked this time.",
    "UNSUPPORTED_CLAIM": "A claim is not backed up by its cited source, so read it with care.",
    "UNSUPPORTED_REMOVED": "One part of the draft was left out because your documents did not back it up.",
    "ANSWER_REGENERATED": "The first draft was not backed up by your documents, so the answer was written again.",
    "UNSUPPORTED_ABSTAINED": "No answer could be written that your documents back up, so none is shown.",
    "TURN_ABANDONED": "This turn stopped unexpectedly, so no answer was written. Try asking again.",
    "META_OP_REJECTED": "That question about your collection was answered from the document content instead.",
    "META_COUNT_DOCUMENTS": "Counted your documents from the document list.",
    "META_LIST_DOCUMENTS": "Listed your documents from the document list.",
    "META_DOCUMENTS_MENTIONING": "Searched the text of your ready documents for the word or phrase.",
    "META_LIST_KEY_DATES": "Listed dates from the verified key facts of your documents.",
    "META_DOCUMENT_INFO": "Read the document details from the document list.",
    "CONFLICT_DETECTED": "The sources differ on a point, so both sides are shown with their citations.",
    "COMPARE_TOO_MANY": "Five is the most documents to compare at once, so this was answered as one question.",
    "COMPARE_SIDE_MISSING": (
        "One document had nothing on this comparison, and the answer says so. "
        "Try adding a document that covers it."
    ),
    "COMPARE_SIDE_NOT_FOUND": (
        "I could not find anything on this comparison in one of the documents, and the answer says so. "
        "Try a document that covers it."
    ),
    "COMPARE_SIDE_REPAIRED": (
        "One document was missing from the first draft, so I looked at it on its own and added what it says."
    ),
    "ELEMENT_NOT_FOUND": (
        "Part of the question had no supporting passage, and the answer says so. "
        "Try a document that covers it."
    ),
    "CALC_CORRECTED": (
        "A figure was recalculated from the numbers in the cited passages, and the answer shows the rechecked result."
    ),
    "CALC_OPERAND_UNVERIFIED": (
        "I could not match every number in a calculation to its cited passage, so that figure is not shown as checked. "
        "Check the passages yourself."
    ),
    "FAST_PATH": "",
    "LOOP_GUARD": "",
    "BAD_ROUTE": "",
    "NODE_TIMEOUT": "The turn ran out of time before it finished. Try again, or ask a narrower question.",
    "PERSIST_FAILED": "",
    "DEGRADED_MODEL_FALLBACK": "A backup was used for part of the work, so the wording may be less polished.",
}

# values of `rag_runs.degraded` (lowercase, set by retrieval)
DEGRADED_TEMPLATES: dict[str, str] = {
    "no_rerank": "Passage ranking was not available, so passages are shown in search order.",
    "lexical_only": "Meaning-based search was not available, so only keyword search was used.",
}


def not_found_text(element: str) -> str:
    return NOT_FOUND_ELEMENT.format(element=element)


CALC_TEXTS = {  # S22.08: the segment text of a checked calculation (code-written; the number is the computed one)
    "add": "Adding {operands} gives {result}.",
    "sum": "Adding {operands} gives {result}.",
    "subtract": "{first} minus {second} is {result}.",
    "ratio": "{first} divided by {second} is {result}.",
    "percent_change": "The change from {first} to {second} is {result}.",
    "average": "The average of {operands} is {result}.",
}


def calc_text(op: str, operands: list[str], result: str) -> str:
    joined = operands[0] if len(operands) == 1 else ", ".join(operands[:-1]) + " and " + operands[-1]
    first, second = [*operands, "", ""][:2]
    return CALC_TEXTS[op].format(operands=joined, first=first, second=second, result=result)


def side_not_found_text(title: str) -> str:
    return SIDE_NOT_FOUND.format(title=title)


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def tokens_label(n: int) -> str:
    if n < 1000:
        return plural(n, "token")
    k = n / 1000
    return f"{k:.0f}k tokens" if k >= 10 else f"{k:.1f}k tokens".replace(".0k", "k")


def answered(sources: list[dict[str, Any]]) -> str | None:
    """The 'answered from ...' line from the run's cited sources (None when nothing was cited)."""
    if not sources:
        return None
    passages = plural(sum(s["chunks_used"] for s in sources), "passage")
    if len(sources) == 1:
        s = sources[0]
        return ANSWERED_ONE_DOC.format(
            passages=passages, title=s["document_title"], version_no=s["version_no"]
        )
    titles = ", ".join(f"'{s['document_title']}' v{s['version_no']}" for s in sources)
    return ANSWERED_MANY_DOCS.format(
        passages=passages, documents=plural(len(sources), "document"), titles=titles
    )


def full_context(documents: int, tokens: int) -> str:
    tpl = FULL_CONTEXT_READ if documents == 1 else FULL_CONTEXT_READ_MANY
    return tpl.format(documents=plural(documents, "document"), tokens=tokens_label(tokens))


def unsupported(count: int) -> str | None:
    if count <= 0:
        return None
    return UNSUPPORTED_ONE if count == 1 else UNSUPPORTED_MANY.format(count=count)
