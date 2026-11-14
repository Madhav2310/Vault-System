"""Pure scope rules."""

import uuid

import pytest

from app.retrieval.scope import Scope, ScopeWideningError, narrow


def _scope(n: int) -> Scope:
    return Scope(entries=tuple(sorted((uuid.uuid4(), uuid.uuid4()) for _ in range(n))))


def test_narrow_rejects_widening() -> None:
    sc = _scope(2)
    outside = uuid.uuid4()
    with pytest.raises(ScopeWideningError):
        narrow(sc, [sc.document_ids[0], outside])
    # a subset narrows; versions follow the documents
    keep = sc.document_ids[0]
    narrowed = narrow(sc, [keep])
    assert narrowed.document_ids == (keep,)
    assert narrowed.version_ids == tuple(v for d, v in sc.entries if d == keep)
    # an empty target list does not widen or shrink
    assert narrow(sc, []) == sc


def test_empty_scope_reason() -> None:
    assert Scope.empty.reason == "SCOPE_EMPTY"
    assert Scope.empty.version_ids == ()
    assert Scope(entries=()).reason == "SCOPE_EMPTY"
    assert _scope(1).reason is None
    # targeting documents outside an empty scope is widening, not a silent empty result
    with pytest.raises(ScopeWideningError):
        narrow(Scope.empty, [uuid.uuid4()])
