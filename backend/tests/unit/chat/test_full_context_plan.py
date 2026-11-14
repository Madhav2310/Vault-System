"""plan_mode: whole-document mode only on request and only when the scope fits the token cap (pure)."""

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


# (docs, intent, requested, tokens, expected mode, expected reason)
CASES: list[tuple[int, str, str, list[int | None], str, str | None]] = [
    (1, "qa", "auto", [10_000], "rag", None),  # not requested: retrieval
    (1, "qa", "full_context", [10_000], "full_context", None),
    (
        4,
        "meta",
        "full_context",
        [100, 100, 100, 100],
        "full_context",
        None,
    ),  # a request skips docs and intent
    (2, "qa", "full_context", [30_000, 30_000], "rag", FULL_CONTEXT_TOO_LARGE),
    (1, "qa", "full_context", [None], "rag", FULL_CONTEXT_TOO_LARGE),  # unknown size is too large
]


@pytest.mark.parametrize("docs,intent,requested,tokens,mode,reason", CASES)
def test_decision_table(
    docs: int, intent: str, requested: str, tokens: list[int | None], mode: str, reason: str | None
) -> None:
    plan = plan_mode(_scope(docs), _an(intent), requested, tokens, _settings())
    assert (plan.mode, plan.reason) == (mode, reason)


def test_empty_scope_is_rag() -> None:
    plan = plan_mode(Scope.empty, _an("qa"), "full_context", [], _settings())
    assert (plan.mode, plan.reason) == ("rag", None)


def test_token_cap_is_inclusive() -> None:
    s = _settings(full_context_max_tokens=1000)
    assert plan_mode(_scope(1), _an("qa"), "full_context", [1000], s).mode == "full_context"
    assert plan_mode(_scope(1), _an("qa"), "full_context", [1001], s).mode == "rag"
