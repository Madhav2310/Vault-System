"""Fast-path rule table (pure, no model)."""

import pytest

from app.chat.routing_rules import FAST_PATH, fast_path

# columns: question, prior_turns, scope_docs, skip_analyze, why
TABLE = [
    ("What is the termination notice period?", 0, 1, True, FAST_PATH),
    ("Who are the parties to the agreement?", 0, 1, True, FAST_PATH),
    ("What was AMD's revenue in fiscal 2023?", 0, 3, True, FAST_PATH),
    ("What is the termination notice period?", 1, 1, False, "history"),
    ("What does it say about liability?", 0, 1, False, "pronoun"),
    ("Is that clause enforceable?", 0, 2, False, "pronoun"),
    ("What did they agree to pay?", 0, 1, False, "pronoun"),
    ("Compare the termination terms", 0, 2, False, "comparison"),
    ("What is the difference in notice periods across the contracts?", 0, 3, False, "comparison"),
    ("Compare the termination terms", 0, 1, True, FAST_PATH),  # one document: nothing to compare across
    ("How many documents do I have?", 0, 1, False, "meta"),
    # follow-ups (prior turns): self-contained ones skip analyze, anything ambiguous is analyzed
    ("What does the lease say about early termination fees?", 2, 3, True, FAST_PATH),
    ("What is the notice period in Section 4.2 of the Master Agreement?", 1, 2, True, FAST_PATH),
    ('What does "force majeure" cover?', 1, 1, True, FAST_PATH),
    ("Who signed the Acme Corp invoice for March?", 3, 4, True, FAST_PATH),
    ("What about the lease?", 1, 2, False, "ellipsis"),
    ("Lease termination fees?", 1, 2, False, "history"),  # too short
    ("And what is the payment term in the contract?", 1, 1, False, "ellipsis"),
    ("Also, what does the lease say about subletting rights?", 1, 2, False, "ellipsis"),
    ("What about the rest of the agreement clauses?", 1, 2, False, "ellipsis"),
    ("Why is the contract fee higher than expected?", 1, 1, False, "ellipsis"),
    ("What does it say about the lease deposit amount?", 1, 1, False, "pronoun"),
    ("Is the same clause in the agreement enforceable?", 2, 2, False, "pronoun"),
    ("Which one of the contract terms matters most?", 1, 2, False, "ellipsis"),
    ("What is the difference between the two lease terms?", 1, 2, False, "comparison"),
    ("How many documents are in the Acme folder?", 1, 2, False, "meta"),
    ("List my documents", 0, 3, False, "meta"),
]


@pytest.mark.parametrize(("question", "prior", "docs", "skip", "why"), TABLE)
def test_fast_path_decisions(question: str, prior: int, docs: int, skip: bool, why: str) -> None:
    d = fast_path(question, prior_turns=prior, scope_docs=docs)
    assert (d.skip_analyze, d.why) == (skip, why)


def test_whole_words_only() -> None:
    # "item", "within" and "other" must not trigger "it"/"the other"; "vsync" is not "vs"
    assert fast_path("What items fall within scope?", prior_turns=0, scope_docs=2).skip_analyze
    assert fast_path("What is vsync?", prior_turns=0, scope_docs=2).skip_analyze


def test_long_or_empty_question_is_analyzed() -> None:
    assert not fast_path("  ", prior_turns=0, scope_docs=1).skip_analyze
    assert not fast_path("x " * 400, prior_turns=0, scope_docs=1).skip_analyze
