"""S3.09 citation resolver."""

import uuid

from app.chat import citations
from app.chat.citations import CITATION_UNRESOLVED, resolve
from app.chat.evidence import EvidenceChunk, build_evidence
from app.ingestion.sentences import split_sentences

A = "Alpha one holds here. Bravo two follows. Charlie three ends. Delta four starts."
B = "Echo five stands alone. Foxtrot six too."


def _ev() -> tuple[object, EvidenceChunk, EvidenceChunk]:
    ca = EvidenceChunk(uuid.uuid4(), uuid.uuid4(), "Doc A", 1, 1, 1, ("S",), A, tuple(split_sentences(A)))
    cb = EvidenceChunk(uuid.uuid4(), uuid.uuid4(), "Doc B", 1, 2, 2, ("S",), B, tuple(split_sentences(B)))
    return build_evidence([ca, cb]), ca, cb


def test_fixture_resolution() -> None:
    ev, ca, cb = _ev()
    out = resolve(
        [
            {"text": "Claim one.", "evidence": ["E1.0", "E2.1"]},
            {"text": "Claim two.", "evidence": ["E1.3"]},
        ],
        ev,  # type: ignore[arg-type]
    )
    s1, s2 = out.segments
    assert (s1.ordinal, s1.text, s1.support) == (0, "Claim one.", "cited")
    r1, r2 = s1.references
    assert (r1.chunk_id, r1.sentence_start, r1.sentence_end, r1.cited_text) == (
        ca.chunk_id,
        0,
        1,
        "Alpha one holds here.",
    )
    assert (r2.chunk_id, r2.version_id, r2.sentence_start, r2.sentence_end, r2.cited_text) == (
        cb.chunk_id,
        cb.version_id,
        1,
        2,
        "Foxtrot six too.",
    )
    assert [(r.sentence_start, r.sentence_end) for r in s2.references] == [(3, 4)]
    assert out.reasons == () and out.unresolved_count == 0


def test_sentence_range() -> None:
    ev, _, _ = _ev()
    out = resolve([{"text": "x", "evidence": ["E1.2", "E1.1", "E1.0", "E1.2"]}], ev)  # type: ignore[arg-type]
    (ref,) = out.segments[0].references  # 0,1,2 adjacent -> one range, end exclusive; duplicates counted once
    assert (ref.sentence_start, ref.sentence_end) == (0, 3)
    assert ref.cited_text == "Alpha one holds here. Bravo two follows. Charlie three ends."
    assert ref.cited_text == A[ref.char_start : ref.char_end]
    gap = resolve([{"text": "x", "evidence": ["E1.0", "E1.2"]}], ev).segments[0].references  # type: ignore[arg-type]
    assert [(r.sentence_start, r.sentence_end) for r in gap] == [(0, 1), (2, 3)]  # not adjacent: two ranges


def test_unresolved_dropped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    added: list[int] = []

    class Counter:
        def add(self, n: int) -> None:
            added.append(n)

    monkeypatch.setattr(citations, "_unresolved", Counter())
    ev, _, _ = _ev()
    out = resolve([{"text": "x", "evidence": ["E1.1", "E7.0", "E1.99", "garbage"]}], ev)  # type: ignore[arg-type]
    seg = out.segments[0]
    assert [r.sentence_start for r in seg.references] == [1] and seg.support == "cited"
    assert seg.dropped_handles == ("E7.0", "E1.99", "garbage")
    assert out.reasons == (CITATION_UNRESOLVED,) and out.unresolved_count == 3 and added == [3]
    resolve([{"text": "x", "evidence": ["E1.0"]}], ev)  # type: ignore[arg-type]
    assert added == [3]  # nothing dropped: the metric is not touched


def test_cited_text_equals_stored_sentences() -> None:
    ev, ca, _ = _ev()
    # the model also writes a "quote": it is not part of the schema, and even an extra field is never used
    seg = {"text": "Alpha one holds here.", "evidence": ["E1.0"], "cited_text": "MODEL-INVENTED QUOTE"}
    ref = resolve([seg], ev).segments[0].references[0]  # type: ignore[arg-type]
    assert ref.cited_text == A[slice(*ca.sentences[0])] == "Alpha one holds here."


def test_uncited_segment_marked() -> None:
    ev, _, _ = _ev()
    out = resolve([{"text": "No source.", "evidence": []}, {"text": "Bad.", "evidence": ["E5.5"]}], ev)  # type: ignore[arg-type]
    assert [(s.text, s.support, s.references) for s in out.segments] == [
        ("No source.", "none", ()),
        ("Bad.", "none", ()),
    ]
    assert (
        out.reasons == (CITATION_UNRESOLVED,) and out.unresolved_count == 1
    )  # an empty list is not unresolved
