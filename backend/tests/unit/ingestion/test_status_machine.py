"""Only the documented document-status transitions are legal."""

import random

import pytest

from app.ingestion import status as vs

EXPECTED_EDGES = {
    ("uploaded", "processing"),
    ("processing", "ready"),
    ("processing", "needs_ocr"),
    ("processing", "unsupported"),
    ("processing", "failed"),
    ("ready", "enriched"),
    ("ready", "superseded"),
    ("enriched", "superseded"),
    ("failed", "processing"),  # retry action
}
ALL = sorted(vs.TRANSITIONS)
# what the runner and later stages may ask for (claim, finish, terminal, enrich, supersede)
REQUESTS = ["processing", "ready", "needs_ocr", "unsupported", "failed", "enriched", "superseded"]


def test_only_legal_transitions() -> None:
    table_edges = {(a, b) for a, bs in vs.TRANSITIONS.items() for b in bs}
    assert table_edges == EXPECTED_EDGES
    rng = random.Random(20261113)
    for _ in range(2000):  # random walks of stage outcomes from a fresh upload
        current, path = "uploaded", ["uploaded"]
        for _ in range(rng.randint(1, 12)):
            to = rng.choice(REQUESTS)
            try:
                vs.check_transition(current, to)
            except vs.IllegalTransitionError:
                assert (current, to) not in EXPECTED_EDGES
                continue
            assert (current, to) in EXPECTED_EDGES, path
            current = to
            path.append(to)
        if current in ("needs_ocr", "unsupported", "superseded"):
            for to in ALL:  # dead ends stay dead
                with pytest.raises(vs.IllegalTransitionError):
                    vs.check_transition(current, to)
