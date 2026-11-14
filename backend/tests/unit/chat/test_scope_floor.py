"""Per-document evidence floor on the plain qa route: a scoped document the global top-n crowded out gets its
own best candidates (no database, no model)."""

import uuid
from types import SimpleNamespace

from app.chat.compare import floor_on, floor_per_doc
from app.core.settings import Settings
from app.retrieval.retrieve import Candidate, RetrievalResult
from app.retrieval.scope import Scope


def _cand(
    version: uuid.UUID, rrf: float, *, selected: bool = False, rerank: float | None = None
) -> Candidate:
    return Candidate(uuid.uuid4(), version, rrf_score=rrf, rerank_score=rerank, selected=selected)


def _result(cands: list[Candidate]) -> RetrievalResult:
    sel = tuple(c.chunk_id for c in cands if c.selected)
    return RetrievalResult(tuple(cands), sel, (), "retrieval@2")


def _scope(n: int) -> tuple[Scope, list[uuid.UUID]]:
    versions = [uuid.uuid4() for _ in range(n)]
    return Scope(entries=tuple((uuid.uuid4(), v) for v in versions)), versions


def test_switch_defaults_off() -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    assert s.chat_scope_floor is False
    assert not floor_on(None) and floor_on(SimpleNamespace(chat_scope_floor=True))


def test_crowded_out_document_gets_its_best_candidates() -> None:
    scope, (a, b) = _scope(2)
    a1, a2, a3 = (
        _cand(a, 0.03, selected=True),
        _cand(a, 0.029, selected=True),
        _cand(a, 0.028, selected=True),
    )
    b_best, b_next, b_low = _cand(b, 0.014), _cand(b, 0.013), _cand(b, 0.010)
    out = floor_per_doc(_result([a1, a2, a3, b_low, b_best, b_next]), scope)
    assert out.selected == (a1.chunk_id, a2.chunk_id, a3.chunk_id, b_best.chunk_id, b_next.chunk_id)
    flagged = {c.chunk_id: c for c in out.candidates}
    assert flagged[b_best.chunk_id].selected and flagged[b_best.chunk_id].result_index == 3
    assert flagged[b_next.chunk_id].result_index == 4
    assert not flagged[b_low.chunk_id].selected


def test_rerank_score_orders_the_top_up_before_fusion_score() -> None:
    scope, (a, b) = _scope(2)
    sel = [_cand(a, 0.03, selected=True), _cand(a, 0.02, selected=True)]
    weak_fused, strong_reranked = _cand(b, 0.02, rerank=0.1), _cand(b, 0.01, rerank=0.9)
    out = floor_per_doc(_result([*sel, weak_fused, strong_reranked]), scope, floor=1)
    assert out.selected[-1] == strong_reranked.chunk_id and len(out.selected) == 3


def test_document_at_the_floor_is_left_alone() -> None:
    scope, (a, b) = _scope(2)
    cands = [_cand(v, 0.03 - i * 0.001, selected=True) for i, v in enumerate((a, a, b, b))] + [
        _cand(b, 0.001)
    ]
    result = _result(cands)
    assert floor_per_doc(result, scope) is result


def test_single_document_or_large_scope_is_unchanged() -> None:
    one, (a,) = _scope(1)
    r1 = _result([_cand(a, 0.03, selected=True), _cand(a, 0.02)])
    assert floor_per_doc(r1, one) is r1
    many, versions = _scope(6)
    r2 = _result([_cand(versions[0], 0.03, selected=True), _cand(versions[1], 0.02)])
    assert floor_per_doc(r2, many) is r2


def test_document_without_candidates_adds_nothing() -> None:
    scope, (a, _b) = _scope(2)
    result = _result([_cand(a, 0.03, selected=True), _cand(a, 0.02, selected=True)])
    assert floor_per_doc(result, scope) is result
