"""Deterministic explanation templates (design §10): one per reason code, rendered from run facts.

The model is never asked to explain itself. Every reason code a turn can record has a template here (a unit
test greps the constants in `app/chat`, `app/retrieval` and `app/llm`); an empty template means the code is
recorded for operators and adds no line for users. Wording never names a model, a prompt or an internal
service, and quotes no document text.
"""

from typing import Any

ANSWERED_ONE_DOC = "Answered from {passages} in '{title}' v{version_no}."
ANSWERED_MANY_DOCS = "Answered from {passages} across {documents}: {titles}."
FULL_CONTEXT_READ = "Read the whole document ({documents}, {tokens})."
FULL_CONTEXT_READ_MANY = "Read the documents in full ({documents}, {tokens})."
UNSUPPORTED_ONE = "One sentence is not supported by its citation."
UNSUPPORTED_MANY = "{count} sentences are not supported by their citations."
NO_ANSWER = "No answer was produced."

# reason code -> user-facing line. Keys are the code strings recorded in `rag_runs.reason_codes`.
REASON_TEMPLATES: dict[str, str] = {
    "NO_EVIDENCE": "Nothing in your documents matched closely enough.",
    "ABSTAIN_NO_EVIDENCE": "The best-matching passages were too weak to answer from, so none was written.",
    "SCOPE_EMPTY": "There are no ready documents in scope. Upload one or wait for processing to finish.",
    "FULL_CONTEXT_UNAVAILABLE": (
        "Whole-document reading was requested but is not available, so the best-matching passages were used."
    ),
    "CLARIFY_AMBIGUOUS_DOCUMENT": "The question could refer to more than one document, so you were asked.",
    "CLARIFY_AMBIGUOUS_QUERY": "The question was too ambiguous to answer, so you were asked to clarify.",
    "CLARIFY_REJECTED": "A clarifying question was not needed, so the question was answered as written.",
    "TARGET_OUT_OF_SCOPE": "A document named in the question is outside this chat's scope and was ignored.",
    "TARGET_IGNORED": "A document named in the question could not be used and was ignored.",
    "ANALYZE_FALLBACK": "The question was used exactly as written because it could not be rewritten.",
    "CITATION_UNRESOLVED": "Some citations could not be matched to your documents and were dropped.",
    "STOP_REFUSAL": "The answer could not be written because the request was declined.",
    "STOP_MAX_TOKENS": "The answer reached its length limit, so only the complete sentences are shown.",
    "GENERATION_INVALID": "The answer came back in an unusable form, so no answer is shown.",
    "TURN_ERROR": "Something went wrong while answering, so no answer was produced.",
    "FULL_CONTEXT_TOO_LARGE": "Too long to read whole, so passages were searched instead.",
    "SUPPORT_CHECK_FAILED": "Citations could not be double-checked this time.",
    "UNSUPPORTED_CLAIM": "A claim is not supported by its cited source.",
    "TURN_ABANDONED": "This turn stopped unexpectedly, so no answer was produced. Ask again.",
    "META_OP_REJECTED": "That question about your collection was answered from document content instead.",
    "META_COUNT_DOCUMENTS": "Counted your documents from the document list.",
    "META_LIST_DOCUMENTS": "Listed your documents from the document list.",
    "META_DOCUMENTS_MENTIONING": "Searched the text of your ready documents for the word or phrase.",
    "META_LIST_KEY_DATES": "Listed dates from the verified key facts of your documents.",
    "META_DOCUMENT_INFO": "Read the document details from the document list.",
    "CONFLICT_DETECTED": "The sources disagree on a point, so both sides are shown with their citations.",
    "COMPARE_TOO_MANY": "Too many documents to compare (limit 5), so this was answered as one question.",
    "COMPARE_SIDE_MISSING": "A document had nothing relevant to the comparison, and the answer says so.",
    "FAST_PATH": "",
    "LOOP_GUARD": "",
    "BAD_ROUTE": "",
    "NODE_TIMEOUT": "The turn ran out of time before it finished.",
    "PERSIST_FAILED": "",
    "DEGRADED_MODEL_FALLBACK": "A backup was used for part of the work; the answer may be less polished.",
}

# values of `rag_runs.degraded` (lowercase, set by retrieval)
DEGRADED_TEMPLATES: dict[str, str] = {
    "no_rerank": "Passage ranking was unavailable, so passages are shown in search order.",
    "lexical_only": "Meaning-based search was unavailable, so only keyword search was used.",
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
