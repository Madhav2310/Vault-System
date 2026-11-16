"""Per-route turn call limits."""

from app.chat.harness import ROUTE_LIMITS


def test_every_answering_route_has_a_call_left_for_the_support_check() -> None:
    """analyze + generate + the support check: a route that answers with citations needs three calls, or the
    check starves on every turn that runs analyze."""
    for route in ("qa", "compare", "summarize"):
        assert ROUTE_LIMITS[route].max_llm_calls >= 3, route


def test_chitchat_runs_on_the_question_route_so_it_can_answer() -> None:
    """A chitchat turn still retrieves and generates, so it needs the question route's call budget (it once
    ran on a single call that the analysis had already spent)."""
    assert "chitchat" not in ROUTE_LIMITS  # unlisted intents run as `qa`, see run_analyze
