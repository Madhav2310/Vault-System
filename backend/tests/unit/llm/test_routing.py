"""Model roles and per-node routing."""

import pytest

from app.core.settings import Settings
from app.llm.routing import (
    DEFAULT_ROUTES,
    EFFORT_SUPPORT,
    ModelRouter,
    Resolved,
    Route,
    RoutingConfigError,
    parse_overrides,
)


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x", **kw)  # type: ignore[call-arg, arg-type]


def _router(**kw: object) -> ModelRouter:
    return ModelRouter.from_settings(_settings(**kw))


LUNA, TERRA, SOL = "gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"

# (node, route, mode) → (role, model, effort)
EXPECTED = [
    (("analyze_query", None, None), ("fast", LUNA, "none")),
    (("evidence_gate", None, None), ("balanced", TERRA, "low")),
    (("generate", "qa", "rag"), ("balanced", TERRA, "none")),
    (("generate", "lookup", "rag"), ("balanced", TERRA, "none")),
    (("generate", "qa", "full_context"), ("balanced", TERRA, "low")),
    (("generate", "compare", "rag"), ("deep", SOL, "low")),
    (("generate", "compare", "full_context"), ("deep", SOL, "low")),
    (("generate", "conflict", "rag"), ("deep", SOL, "low")),
    (("brief_extract", None, None), ("balanced", TERRA, "low")),
    (("summary", None, None), ("balanced", TERRA, "low")),
    (("followups", None, None), ("fast", LUNA, "none")),
    (("memory_compact", None, None), ("fast", LUNA, "none")),
    (("tag_category", None, None), ("fast", LUNA, "none")),
    (("contextualize", None, None), ("fast", LUNA, "none")),
    (("matrix_cell", None, None), ("balanced", TERRA, "low")),
    (("eval_judge", None, None), ("deep", SOL, "low")),
]


def test_default_routes() -> None:
    r = _router()
    for (node, route, mode), (role, model, effort) in EXPECTED:
        assert r.resolve(node, route, mode) == Resolved(role, model, effort), (node, route, mode)  # type: ignore[arg-type]
    # every table row is exercised by the cases above
    exercised = {(n, rt, m) for (n, rt, m), _ in EXPECTED}
    for key in DEFAULT_ROUTES:
        node, _, rest = key.partition(":")
        route, _, mode = rest.partition(":")
        assert any(
            n == node and (not route or rt == route) and (not mode or m == mode) for n, rt, m in exercised
        ), key
    # no matching row → the prompt's front-matter default applies; no default → error
    assert r.resolve("unlisted", default=Route("balanced", "medium")) == Resolved("balanced", TERRA, "medium")
    with pytest.raises(RoutingConfigError, match="unlisted"):
        r.resolve("unlisted")


def test_sol_rejects_none() -> None:
    assert "none" not in EFFORT_SUPPORT[SOL]
    with pytest.raises(RoutingConfigError, match="analyze_query") as e:
        _router(model_fast=SOL)  # the default analyze_query route uses effort none
    assert SOL in str(e.value) and "none" in str(e.value)
    with pytest.raises(RoutingConfigError, match="generate:compare"):
        _router(model_routes={"generate:compare": {"role": "deep", "effort": "none"}})
    # a prompt default that pairs sol with none fails at resolve time and names the node
    with pytest.raises(RoutingConfigError, match="node 'x'"):
        _router().resolve("x", default=Route("deep", "none"))


def test_override_and_validation() -> None:
    r = _router(model_routes={"analyze_query": {"role": "balanced", "effort": "low"}})
    assert r.resolve("analyze_query") == Resolved("balanced", TERRA, "low")
    assert r.resolve("followups") == Resolved("fast", LUNA, "none")  # others unchanged
    # a role → model remap flows through the table
    assert _router(model_balanced="gpt-6-astra").resolve("summary") == Resolved(
        "balanced", "gpt-6-astra", "low"
    )
    with pytest.raises(RoutingConfigError, match="route 'generate'"):  # sol cannot serve the effort-none rows
        _router(model_balanced=SOL)
    # an explicit model pin on one node
    pin = _router(model_routes={"summary": {"role": "fast", "effort": "low", "model": TERRA}})
    assert pin.resolve("summary").model == TERRA
    # specificity: node:route beats node::mode beats node
    sp = _router(model_routes={"generate:qa": {"role": "deep", "effort": "low"}})
    assert sp.resolve("generate", "qa", "full_context").role == "deep"
    assert sp.resolve("generate", "lookup", "full_context").role == "balanced"

    bad: list[dict[str, dict[str, str]]] = [
        {"analyze_query": {"role": "turbo", "effort": "low"}},
        {"analyze_query": {"role": "fast", "effort": "extreme"}},
        {"analyze_query": {"role": "fast"}},
        {"analyze_query": {"role": "fast", "effort": "low", "model": "gpt-9-imaginary"}},
        {"analyze_query": {"role": "fast", "effort": "low", "temperature": "0"}},
    ]
    for raw in bad:
        with pytest.raises(RoutingConfigError, match="analyze_query"):
            _router(model_routes=raw)
    with pytest.raises(RoutingConfigError, match="gpt-9"):
        _router(model_flagship="gpt-9-imaginary")
    assert parse_overrides({}) == {}


def test_fallback_role() -> None:
    r = _router()
    assert r.fallback(Resolved("fast", LUNA, "none")) == Resolved("balanced", TERRA, "none")
    assert r.fallback(Resolved("balanced", TERRA, "low")) == Resolved("deep", SOL, "low")
    # the fallback model may not support the effort: use its lowest supported one, never an error
    assert r.fallback(Resolved("balanced", TERRA, "none")) == Resolved("deep", SOL, "low")
    assert r.fallback(Resolved("deep", SOL, "low")) is None
    assert r.fallback(Resolved("flagship", "gpt-6-astra", "low")) is None


def test_app_and_worker_validate_routes_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import settings as settings_mod
    from app.main import create_app
    from app.worker.celery_app import make_celery

    bad = _settings(model_fast=SOL)  # analyze_query pairs `fast` with effort none
    monkeypatch.setattr(settings_mod, "get_settings", lambda: bad)
    monkeypatch.setattr("app.main.get_settings", lambda: bad)
    monkeypatch.setattr("app.worker.celery_app.get_settings", lambda: bad)
    with pytest.raises(RoutingConfigError, match="analyze_query"):
        create_app()
    with pytest.raises(RoutingConfigError, match="analyze_query"):
        make_celery()
