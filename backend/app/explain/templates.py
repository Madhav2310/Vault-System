"""Deterministic explanation templates: one per reason code, rendered from run facts.

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

# Code-written responses (docs/voice.md). Always on: they are deterministic, the model writes none of them.
NO_DOCUMENTS_TEXT = (
    "There are no ready documents to search yet. Upload one, or give processing a moment to finish."
)
NO_EVIDENCE_TEXT = (
    "I could not find this in your documents. "
    "Try asking in different words, or name the document or section you have in mind."
)
CLARIFY_TEXT = "More than one of your documents could answer this. Which one should I look in?"
ERROR_TEXT = "That did not work on my side, and no answer was written. Please try again in a moment."
QUOTA_REACHED = "You have reached today's usage limit. It resets in {retry} s (at midnight UTC), so you can ask again then."
RATE_LIMITED = "That was a lot of requests at once. Please try again in {retry} s."
STALE_ANSWER = (
    "A newer version of this document was added after this answer. Ask again to see if anything changed."
)

# reason code -> user-facing line. Keys are the code strings recorded in `rag_runs.reason_codes`.
REASON_TEMPLATES: dict[str, str] = {
    "NO_EVIDENCE": "Nothing in your documents matched closely enough. Try different words or name the document.",
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
