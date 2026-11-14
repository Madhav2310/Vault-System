"""S23.02 ambiguity rules: pure code, no model."""

import uuid

from app.chat.clarify import (
    CLARIFY_NO_ANTECEDENT,
    MAX_OPTIONS,
    build_options,
    has_reference_without_antecedent,
    near_tie,
    no_antecedent,
)
from app.chat.nodes.analyze import CLARIFY_AMBIGUOUS_DOCUMENT
from app.explain.templates import CLARIFY_TEXT, REASON_TEMPLATES

A, B, C, D, E = (uuid.uuid4() for _ in range(5))
DOCS = [(A, "Acme agreement"), (B, "Beta agreement")]


def test_reference_detection() -> None:
    assert has_reference_without_antecedent("When does it expire?")
    assert has_reference_without_antecedent("What is the payment term in this contract?")
    assert not has_reference_without_antecedent("What is the notice period?")
    assert not has_reference_without_antecedent("Who are the parties to the Acme agreement?")


def test_no_antecedent_needs_everything() -> None:
    c = no_antecedent("When does it expire?", prior_turns=0, scope_docs=DOCS)
    assert c is not None and c.reason == CLARIFY_NO_ANTECEDENT
    assert [o.document_id for o in c.options] == [A, B]
    assert c.text == CLARIFY_TEXT
    # each condition alone removes the clarification
    assert no_antecedent("When does it expire?", prior_turns=1, scope_docs=DOCS) is None  # has history
    assert no_antecedent("When does it expire?", prior_turns=0, scope_docs=DOCS[:1]) is None  # one document
    assert no_antecedent("When does it expire?", prior_turns=0, scope_docs=DOCS, narrowed=True) is None
    assert no_antecedent("What is the notice period?", prior_turns=0, scope_docs=DOCS) is None  # no reference
    assert (
        no_antecedent("When does the Acme agreement expire? Is it long?", prior_turns=0, scope_docs=DOCS)
        is None
    )


def test_no_antecedent_large_scope_uses_only_real_hinted_titles() -> None:
    docs = [(uuid.uuid4(), f"Doc {i}") for i in range(6)]
    q = "When does it expire?"
    assert no_antecedent(q, prior_turns=0, scope_docs=docs) is None  # too many to list
    hinted = ["Doc 2", "Doc 4", "Invented"]
    c = no_antecedent(q, prior_turns=0, scope_docs=docs, hinted_titles=hinted)
    assert c is not None and [o.label for o in c.options] == [
        "Doc 2",
        "Doc 4",
    ]  # invented titles never appear


def test_options_are_capped_and_repeated_titles_get_versions() -> None:
    opts = build_options([(x, f"T{i}", 1) for i, x in enumerate((A, B, C, D, E))])
    assert len(opts) == MAX_OPTIONS
    same = build_options([(A, "Lease", 1), (B, "Lease", 2)])
    assert [o.label for o in same] == ["Lease (v1)", "Lease (v2)"]


META = {A: ("Acme agreement", 1), B: ("Beta agreement", 2), C: ("Gamma agreement", 1)}


def test_near_tie_needs_hint_and_close_scores() -> None:
    c = near_tie({A: 1.0, B: 0.95, C: 0.2}, META, narrowed=False, hinted=True)
    assert c is not None and c.reason == CLARIFY_AMBIGUOUS_DOCUMENT
    assert [o.document_id for o in c.options] == [A, B]  # the far third is not offered
    assert near_tie({A: 1.0, B: 0.95}, META, narrowed=False, hinted=False) is None  # no model hint
    assert near_tie({A: 1.0, B: 0.95}, META, narrowed=True, hinted=True) is None  # scope already narrowed
    assert near_tie({A: 1.0, B: 0.5}, META, narrowed=False, hinted=True) is None  # one clear winner
    assert near_tie({A: 1.0}, META, narrowed=False, hinted=True) is None  # one document
    assert near_tie({A: -1.0, B: -1.0}, META, narrowed=False, hinted=True) is None  # unusable scale


def test_templates_exist_and_are_warm() -> None:
    for code in (CLARIFY_NO_ANTECEDENT, CLARIFY_AMBIGUOUS_DOCUMENT):
        assert REASON_TEMPLATES[code]
    assert "!" not in CLARIFY_TEXT and "Unfortunately" not in CLARIFY_TEXT
