"""Per-route turn call limits."""


def test_every_answering_route_has_a_call_left_for_the_support_check() -> None:
    """analyze + generate + the support check: a route that answers with citations needs three calls, or the
    check starves on every turn that runs analyze (it did on the summarize route with a limit of 2)."""
    from app.chat.harness import ROUTE_LIMITS

    for route in ("qa", "compare", "summarize"):
        assert ROUTE_LIMITS[route].max_llm_calls >= 3, route
