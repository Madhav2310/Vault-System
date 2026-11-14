"""Named-document retrieval (switch `chat_named_docs`): a question that names two or more scoped documents
("the Hollis motor policy, the Fibrenet credits and the Norden warranty") retrieves once per named document
and merges each document's best chunks into the global selection.

Without it the fast path runs ONE global top-n over the scope, and a named document whose wording is a weak
match can get no chunk at all (a per-document floor cannot help: the chunk was never a candidate).

Detection is plain code, no model call. A scoped document is *named* when the question contains a word that
is distinctive to that document among the scoped documents. Its name words are the words of its stored title
(file stems and camel-case names are split; glued upper-case names match by prefix), the words of its opening
heading, and the capitalised words of the rest of its opening text (`ScopeDoc.lead`: where "Hollis Courier
Services" or "Norden Appliances" live, as the stored title is often a file stem such as `insurance_summary`).
Title and heading words name it in any case; body words only when the question writes them as a capitalised
name. A word shared by more than `max_df` documents (a generic word, or a name common to a document family)
names nothing. Documents the question describes without a name ("the quarterly sales summaries") are not
detected.
"""

import asyncio
import dataclasses
import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.nodes.analyze import ScopeDoc
from app.retrieval.query_embed import QueryEmbedding
from app.retrieval.retrieve import RetrievalResult, Retriever
from app.retrieval.scope import Scope

MIN_NAMED = 2
MAX_NAMED = 5
PER_DOC = 3
MAX_DF = 1
CONCURRENCY = 3
LEAD_CHARS = 400
HEADING_CHARS = 120  # the first line of the opening text is the document's own heading
MIN_TOKEN = 3
MIN_LEAD_TOKEN = 4
GLUED_LEN = 14  # a title word this long is a glued name (`entertainmentgamingasiainc`): prefixes match
MIN_PREFIX = 4
MIN_CONCAT = 6
MIN_PART = 5  # shortest camel-case part of a title word that names a document on its own


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_STOP = _words(
    "the a an and or of for to in on at by with from as is are was were be been this that these those it its "
    "do does did how what which who whom when where why under over than then also any all each both more "
    "most much many some such not no nor can could should would may might must will shall have has had their "
    "there they them our your his her one two out into per via vs versus between about after before during "
    "within without across against again further once only same other own very too just"
)
_SUFFIXES = ("holdings", "holding", "inc", "corp", "ltd", "llc", "plc")
_VERSION = re.compile(r"^(v\d+|amend\d*|\d+[a-z]{0,2}\d*|q\d)$")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ALNUM = re.compile(r"[A-Za-z0-9]+")
_CAP = re.compile(r"\b[A-Z][a-zA-Z]{3,}\b")
_SPELL = {"licence": "license", "licences": "licenses"}


def named_on(settings: Any) -> bool:
    return bool(getattr(settings, "chat_named_docs", False))


def _norm(tok: str) -> str:
    tok = _SPELL.get(tok, tok)
    return tok[:-1] if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss") else tok


_GENERIC = frozenset(
    _norm(w)
    for w in _words(
        "january february march april may june july august september october november december monday "
        "tuesday wednesday thursday friday saturday sunday inc corp corporation ltd llc plc company co "
        "group holdings holding limited ex exhibit amend amendment agreement contract document report "
        "policy terms summary services service addendum order form"
    )
)


def _keep(tok: str) -> bool:
    return len(tok) >= MIN_TOKEN and tok not in _STOP and tok not in _GENERIC and not _VERSION.match(tok)


@dataclasses.dataclass(frozen=True)
class DocNames:
    title: frozenset[str]  # words of the stored title and opening heading: they name it in any case
    lead: frozenset[str]  # capitalised words of its opening text: they name it only as a capitalised name

    @property
    def all(self) -> frozenset[str]:
        return self.title | self.lead


def doc_names(doc: ScopeDoc) -> DocNames:
    title: set[str] = set()
    for raw in _ALNUM.findall(doc.title):
        parts = [p for p in _CAMEL.split(raw) if len(p) >= MIN_PART] if raw != raw.upper() else []
        forms = [raw, *parts]
        forms += [
            f[: -len(sfx)]
            for f in forms
            for sfx in _SUFFIXES
            if f.lower().endswith(sfx) and len(f) > len(sfx) + 3
        ]
        title |= {t for t in (_norm(f.lower()) for f in forms) if _keep(t)}
    heading, _, body = doc.lead[:LEAD_CHARS].lstrip().partition("\n")
    title |= {
        t
        for t in (_norm(w.lower()) for w in _ALNUM.findall(heading[:HEADING_CHARS]))
        if len(t) >= MIN_LEAD_TOKEN and _keep(t)
    }
    lead = {_norm(m.lower()) for m in _CAP.findall(body)}
    return DocNames(
        frozenset(title), frozenset(t for t in lead if len(t) >= MIN_LEAD_TOKEN and _keep(t)) - title
    )


def _lead_words(doc: ScopeDoc) -> frozenset[str]:
    return frozenset(_norm(w.lower()) for w in _ALNUM.findall(doc.lead[:LEAD_CHARS]))


def _question_tokens(question: str) -> list[tuple[str, bool]]:
    """Content tokens in order, each with whether the question writes it as a capitalised name (an upper-case
    initial that does not start a sentence)."""
    out: list[tuple[str, bool]] = []
    for m in _ALNUM.finditer(question):
        before = question[: m.start()].rstrip()
        cap = m.group()[0].isupper() and bool(before) and before[-1] not in ".?!:;"
        tok = _norm(m.group().lower())
        if tok not in _STOP:
            out.append((tok, cap))
    return out


def _concats(toks: Sequence[str]) -> set[str]:
    """ "Entertainment Gaming Asia" also reads as `entertainmentgamingasia`, the glued title word's prefix."""
    found = {"".join(toks[i : i + n]) for n in (2, 3, 4) for i in range(len(toks) - n + 1)}
    return {c for c in found if len(c) >= MIN_CONCAT}


def _title_hit(name: str, q: str) -> bool:
    return name == q or (len(name) >= GLUED_LEN and len(q) >= MIN_PREFIX and name.startswith(q))


def detect_named(
    question: str, docs: Sequence[ScopeDoc], *, max_df: int = MAX_DF, limit: int = MAX_NAMED
) -> list[uuid.UUID]:
    """Ids of the documents the question names, in the order the question first names them (at most `limit`).
    `docs` is the whole scope, so a token's document frequency is measured against every scoped document."""
    names = {d.id: doc_names(d) for d in docs}
    df: dict[str, int] = {}
    for d in docs:
        for t in names[d.id].all | _lead_words(d):  # a word in several openings is a common word
            df[t] = df.get(t, 0) + 1
    qtoks = _question_tokens(question)
    concats = _concats([t for t, _ in qtoks])
    first: dict[uuid.UUID, int] = {}
    for d in docs:
        title = [t for t in names[d.id].title if df[t] <= max_df]
        lead = [t for t in names[d.id].lead if df[t] <= max_df]
        pos = [
            i
            for i, (q, cap) in enumerate(qtoks)
            if any(_title_hit(t, q) for t in title) or (cap and q in lead)
        ]
        if pos:
            first[d.id] = pos[0]
        elif any(t in concats or (len(t) >= GLUED_LEN and t.startswith(c)) for t in title for c in concats):
            first[d.id] = len(qtoks)
    return [d for d, _ in sorted(first.items(), key=lambda kv: (kv[1], str(kv[0])))][:limit]


async def retrieve_named(
    retriever: Retriever,
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    scope: Scope,
    named: Sequence[uuid.UUID],
    query: str,
    *,
    embedding: QueryEmbedding | None = None,
) -> list[tuple[uuid.UUID, RetrievalResult]]:
    """`(version_id, result)` per named document, in the order named: one retrieval each (concurrent,
    `CONCURRENCY` at a time) with the same query and the scope narrowed to that document. A failed retrieval
    is dropped: the global result still stands."""
    emb = embedding or await retriever.embed(query, owner_id)
    sem = asyncio.Semaphore(CONCURRENCY)
    entries = [e for d in named for e in scope.entries if e[0] == d]

    async def one(entry: tuple[uuid.UUID, uuid.UUID]) -> RetrievalResult | None:
        async with sem:
            try:
                return await retriever.retrieve(sm, owner_id, Scope(entries=(entry,)), query, embedding=emb)
            except Exception:  # the global selection is still a valid answer basis
                return None

    results = await asyncio.gather(*(one(e) for e in entries))
    return [(v, r) for (_, v), r in zip(entries, results, strict=True) if r is not None]


def merge_named(
    result: RetrievalResult, per_doc: Sequence[tuple[uuid.UUID, RetrievalResult]], *, k: int = PER_DOC
) -> RetrievalResult:
    """Top each named document up to `k` selected chunks: its best chunks (its own retrieval order) that the
    global selection lacks are appended after it, in the order the documents were named. An added chunk keeps
    the scores of the per-document retrieval as its `retrieval_candidates` row; a chunk the global result
    already scored keeps its global row. Plain code, deterministic."""
    version_of = {c.chunk_id: c.version_id for r in (result, *(r for _, r in per_doc)) for c in r.candidates}
    chosen = set(result.selected)
    added: list[uuid.UUID] = []
    for version, r in per_doc:
        have = sum(1 for cid in chosen if version_of.get(cid) == version)
        for cid in r.selected:
            if have >= k:
                break
            if cid not in chosen:
                added.append(cid)
                chosen.add(cid)
                have += 1
    if not added:
        return result
    index = {cid: len(result.selected) + i for i, cid in enumerate(added)}
    known = {c.chunk_id for c in result.candidates}
    new_rows = {c.chunk_id: c for _, r in per_doc for c in r.candidates if c.chunk_id in index}
    candidates = [
        dataclasses.replace(c, selected=True, result_index=index[c.chunk_id]) if c.chunk_id in index else c
        for c in result.candidates
    ]
    candidates += [
        dataclasses.replace(new_rows[cid], selected=True, result_index=index[cid])
        for cid in added
        if cid not in known
    ]
    degraded = tuple(dict.fromkeys((*result.degraded, *(d for _, r in per_doc for d in r.degraded))))
    return dataclasses.replace(
        result, candidates=tuple(candidates), selected=(*result.selected, *added), degraded=degraded
    )


async def apply_named(
    retriever: Retriever,
    sm: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    docs: Sequence[ScopeDoc],
    scope: Scope,
    query: str,
    result: RetrievalResult,
    *,
    embedding: QueryEmbedding | None = None,
    k: int = PER_DOC,
) -> tuple[RetrievalResult, dict[str, int]]:
    """The turn step: detect the documents `query` names among `docs`, keep those still in `scope`, and with
    two or more retrieve each and merge. Returns the result and the counts for the step's decision."""
    in_scope = set(scope.document_ids)
    named = [d for d in detect_named(query, docs) if d in in_scope]
    info = {"named_docs": len(named)}
    if len(named) < MIN_NAMED:
        return result, info
    per_doc = await retrieve_named(retriever, sm, owner_id, scope, named, query, embedding=embedding)
    merged = merge_named(result, per_doc, k=k)
    info["named_added"] = len(merged.selected) - len(result.selected)
    return merged, info
