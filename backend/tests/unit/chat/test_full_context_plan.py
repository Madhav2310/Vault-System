"""S17.07 plan_mode decision table (pure)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.chat.full_context import FULL_CONTEXT_TOO_LARGE, plan_mode
from app.core.settings import Settings
from app.retrieval.scope import Scope


def _scope(n: int) -> Scope:
    return Scope(entries=tuple((uuid.uuid4(), uuid.uuid4()) for _ in range(n)))


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x", **kw)  # type: ignore[call-arg]


def _an(intent: str) -> Any:
    return SimpleNamespace(intent=intent)


# (docs, intent, requested, tokens, auto, expected mode, expected reason)
CASES: list[tuple[int, str, str, list[int | None], bool, str, str | None]] = [
    (1, "qa", "auto", [10_000], False, "rag", None),  # auto is off
    (1, "qa", "auto", [10_000], True, "full_context", None),
    (3, "compare", "auto", [10_000, 10_000, 10_000], True, "full_context", None),
    (4, "qa", "auto", [1, 1, 1, 1], True, "rag", None),  # too many documents
    (1, "meta", "auto", [100], True, "rag", None),  # intent not eligible
    (1, "qa", "auto", [50_001], True, "rag", None),  # auto and too large: silent RAG
    (1, "qa", "full_context", [10_000], False, "full_context", None),  # requested wins over auto=false
    (
        4,
        "meta",
        "full_context",
        [100, 100, 100, 100],
        False,
        "full_context",
        None,
    ),  # requested skips docs/intent
    (2, "qa", "full_context", [30_000, 30_000], False, "rag", FULL_CONTEXT_TOO_LARGE),
    (1, "qa", "full_context", [None], False, "rag", FULL_CONTEXT_TOO_LARGE),  # unknown size is too large
]


@pytest.mark.parametrize("docs,intent,requested,tokens,auto,mode,reason", CASES)
def test_decision_table(
    docs: int,
    intent: str,
    requested: str,
    tokens: list[int | None],
    auto: bool,
    mode: str,
    reason: str | None,
) -> None:
    plan = plan_mode(_scope(docs), _an(intent), requested, tokens, _settings(full_context_auto=auto))
    assert (plan.mode, plan.reason) == (mode, reason)


def test_empty_scope_is_rag() -> None:
    plan = plan_mode(Scope.empty, _an("qa"), "full_context", [], _settings(full_context_auto=True))
    assert (plan.mode, plan.reason) == ("rag", None)


def test_boundary_is_inclusive() -> None:
    s = _settings(full_context_auto=True, full_context_max_tokens=1000)
    assert plan_mode(_scope(1), _an("qa"), "auto", [1000], s).mode == "full_context"
    assert plan_mode(_scope(1), _an("qa"), "auto", [1001], s).mode == "rag"
