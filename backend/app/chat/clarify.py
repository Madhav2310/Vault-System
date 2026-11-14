"""Clarify when ambiguous: the human in the loop, decided by code.

The model never decides to ask and never writes the question or the options. Two rules fire, both only when
`chat_clarify` is on and the previous assistant message was not itself a clarification (no loops):

* no antecedent: a pronoun or "this contract" with no history and no document named in the question, while the
  scope holds 2-4 documents (or the analysis hint names real titles of a larger scope);
* near tie: the analysis returned a typed `clarification` hint (kind `document`) AND retrieval over an
  unnarrowed scope found the top evidence in 2+ documents with near-equal scores.

Options are real document titles (<= 4); the question text is a template. A chosen option is posted as a
normal turn whose scope is narrowed by code (`rag_runs.analysis.clarify_pick`).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.chat.nodes.analyze import CLARIFY_AMBIGUOUS_DOCUMENT
from app.explain.templates import CLARIFY_TEXT

CLARIFY_NO_ANTECEDENT = "CLARIFY_NO_ANTECEDENT"

MAX_OPTIONS = 4
NEAR_TIE = 0.9  # runner-up document score / top document score
MAX_LABEL_CHARS = 120

# "this contract", "the document", or a bare pronoun that needs an antecedent
_REFERENCE = re.compile(
    r"\b(?:(?:this|that|the|these|those)\s+(?:document|contract|agreement|file|report|paper|pdf|policy|lease)s?"
    r"|it|its|they|them|their|this|that|these|those)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClarifyOption:
    label: str
    document_id: uuid.UUID


@dataclass(frozen=True)
class Clarification:
    kind: Literal["document"]
    reason: str
    options: tuple[ClarifyOption, ...]

    @property
    def text(self) -> str:
        return CLARIFY_TEXT

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "options": [{"label": o.label, "document_id": str(o.document_id)} for o in self.options],
        }


def has_reference_without_antecedent(question: str) -> bool:
    return _REFERENCE.search(question) is not None


def names_a_document(question: str, titles: Sequence[str]) -> bool:
    q = " ".join(question.lower().split())
    return any(t.strip() and " ".join(t.lower().split()) in q for t in titles)


def _label(title: str) -> str:
    t = " ".join(title.split())
    return t if len(t) <= MAX_LABEL_CHARS else t[: MAX_LABEL_CHARS - 1].rstrip() + "…"


def build_options(docs: Sequence[tuple[uuid.UUID, str, int | None]]) -> tuple[ClarifyOption, ...]:
    """Options from real (document id, title, version number) triples; a repeated title gets its version."""
    items = list(dict.fromkeys(docs))[:MAX_OPTIONS]
    count: dict[str, int] = {}
    for _, t, _v in items:
        count[_label(t)] = count.get(_label(t), 0) + 1
    out = []
    for doc_id, title, version in items:
        label = _label(title)
        if count[label] > 1 and version is not None:
            label = f"{label} (v{version})"
        out.append(ClarifyOption(label, doc_id))
    return tuple(out)


def no_antecedent(
    question: str,
    *,
    prior_turns: int,
    scope_docs: Sequence[tuple[uuid.UUID, str]],
    hinted_titles: Sequence[str] = (),
    narrowed: bool = False,
) -> Clarification | None:
    """Rule (b): a reference with nothing to refer to, over 2+ documents, with real documents to pick from."""
    if prior_turns > 0 or narrowed or len(scope_docs) < 2:
        return None
    if not has_reference_without_antecedent(question) or names_a_document(
        question, [t for _, t in scope_docs]
    ):
        return None
    pool = list(scope_docs)
    if len(pool) > MAX_OPTIONS:  # too many to list: only titles the analysis named, checked against the scope
        by_title = {_label(t): (d, t) for d, t in scope_docs}
        pool = [by_title[h] for h in dict.fromkeys(hinted_titles) if h in by_title]
    if len(pool) < 2:
        return None
    return Clarification("document", CLARIFY_NO_ANTECEDENT, build_options([(d, t, None) for d, t in pool]))


def near_tie(
    scored: Mapping[uuid.UUID, float],
    meta: Mapping[uuid.UUID, tuple[str, int | None]],
    *,
    narrowed: bool,
    hinted: bool,
) -> Clarification | None:
    """Rule (a): the evidence is spread over several documents with near-equal scores.

    `scored` maps a document
    id to the best score of its selected evidence; `meta` maps it to (title, version number)."""
    if narrowed or not hinted or len(scored) < 2:
        return None
    ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[0][1]
    if top <= 0:
        return None
    close = [d for d, s in ranked if s >= NEAR_TIE * top and d in meta]
    options = build_options([(d, meta[d][0], meta[d][1]) for d in close])
    if len(options) < 2:
        return None
    return Clarification("document", CLARIFY_AMBIGUOUS_DOCUMENT, options)
