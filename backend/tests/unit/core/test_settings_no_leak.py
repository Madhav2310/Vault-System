"""A settings validation error must never echo input values (the environment holds API keys)."""

import pytest
from pydantic import ValidationError

from app.core.settings import Settings


def test_validation_error_hides_input(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-test-SECRET-VALUE-0123456789"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValidationError) as e:
        Settings(_env_file=None, openai_api_key=secret)  # type: ignore[call-arg]
    assert secret not in str(e.value)
    assert "sk-test" not in str(e.value)


def test_version_switches_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHAT_SUPPORT_VERSION=2 / CHAT_ANALYZE_VERSION=3 in the environment (strings) must work."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("REDIS_URL", "redis://h/0")
    monkeypatch.setenv("CHAT_SUPPORT_VERSION", "2")
    monkeypatch.setenv("CHAT_ANALYZE_VERSION", "3")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.chat_support_version == 2 and s.chat_analyze_version == 3


def test_answer_version_defaults_to_the_accepted_prompt_and_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.chat.nodes.generate import COMPLETE_PROMPT, PROMPT, answer_prompt
    from app.llm.prompts import default_registry

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("REDIS_URL", "redis://h/0")
    monkeypatch.delenv("CHAT_ANSWER_VERSION", raising=False)
    assert answer_prompt(Settings(_env_file=None)) == COMPLETE_PROMPT  # type: ignore[call-arg]
    monkeypatch.setenv("CHAT_ANSWER_VERSION", "3")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.chat_answer_version == 3 and answer_prompt(s) == PROMPT
    body = default_registry().get(*COMPLETE_PROMPT).body
    assert "every part" in body and "answer for that version only" in body
