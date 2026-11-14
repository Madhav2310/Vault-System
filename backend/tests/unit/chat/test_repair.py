"""S22.06 pure parts: handle subset validation, pruning by code, removal, contradiction. No model calls."""

import uuid

from app.chat.citations import Resolution, resolve
from app.chat.evidence import EvidenceChunk, EvidenceSet, build_evidence
from app.chat.support_check import (
    UNCHECKED,
    SupportOutV2,
    Verdict,
    VerdictOutV2,
    all_failed,
    apply_verdicts_v2,
    build_inputs_v2,
    repair,
)
from app.ingestion.sentences import split_sentences

BODY = "Alpha is first. Beta is second. Gamma is third. Delta is fourth."


def _evidence() -> EvidenceSet:
    chunk = EvidenceChunk(
        chunk_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        doc_title="Doc",
        version_no=1,
        page_start=1,
        page_end=1,
        section_path=(),
        original_text=BODY,
        sentences=tuple(tuple(se) for se in split_sentences(BODY)),  # type: ignore[arg-type]
    )
    return build_evidence([chunk])


def _resolved(ev: EvidenceSet, *segs: tuple[str, list[str]]) -> Resolution:
    return resolve([{"text": t, "evidence": h} for t, h in segs], ev)


def _out(*items: tuple[int, str, list[str], bool]) -> SupportOutV2:
    return SupportOutV2(
        verdicts=[
            VerdictOutV2(index=i, verdict=v, contradicts=c, needed=n, reason="r")  # type: ignore[arg-type]
            for i, v, n, c in items
        ]
    )


def test_invented_handles_rejected() -> None:
    ev = _evidence()
    res = _resolved(ev, ("Alpha and Gamma.", ["E1.0", "E1.2"]))
    inputs = build_inputs_v2(res.segments, ev)
    shown = [c["handle"] for c in inputs["segments"][0]["cited"]]  # type: ignore[attr-defined, index]
    assert shown == ["E1.0", "E1.2"]
    got = apply_verdicts_v2(res.segments, _out((0, "supported", ["E1.2", "E1.3", "E9.9"], False)), inputs)
    assert got[0].needed == ("E1.2",)  # E1.3 was never cited here and E9.9 does not exist: never added
    only_invented = apply_verdicts_v2(res.segments, _out((0, "supported", ["E1.3"], False)), inputs)
    assert only_invented[0].needed is None  # nothing valid left: keep every citation, never prune to zero
    everything = apply_verdicts_v2(res.segments, _out((0, "supported", ["E1.0", "E1.2"], False)), inputs)
    assert everything[0].needed is None  # nothing to prune


def test_prune_by_code_and_remove() -> None:
    ev = _evidence()
    res = _resolved(
        ev,
        ("Alpha.", ["E1.0", "E1.1", "E1.3"]),
        ("Wrong.", ["E1.2"]),
        ("Contradicted.", ["E1.2"]),
    )
    inputs = build_inputs_v2(res.segments, ev)
    verdicts = apply_verdicts_v2(
        res.segments,
        _out((0, "supported", ["E1.1"], False), (1, "unsupported", [], False), (2, "unsupported", [], True)),
        inputs,
    )
    fixed = repair(res.segments, verdicts, ev)
    assert fixed.removed == (1,) and fixed.pruned == 1
    assert [s.ordinal for s in fixed.segments] == [0, 2]  # ordinals are kept (the stream used them)
    assert [(r.sentence_start, r.sentence_end, r.cited_text) for r in fixed.segments[0].references] == [
        (1, 2, "Beta is second.")
    ]  # cited_text is re-copied from the stored sentence
    assert fixed.segments[1].references == res.segments[2].references  # contradicted: kept, untouched


def test_all_failed() -> None:
    ev = _evidence()
    res = _resolved(ev, ("a", ["E1.0"]), ("b", ["E1.1"]))
    assert all_failed(res.segments, {0: Verdict("unsupported"), 1: Verdict("unsupported")})
    assert not all_failed(res.segments, {0: Verdict("unsupported"), 1: Verdict("partial")})
    assert not all_failed(res.segments, {0: Verdict("unsupported"), 1: Verdict(UNCHECKED)})
    assert not all_failed((), {})
