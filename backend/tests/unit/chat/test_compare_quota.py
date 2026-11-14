"""S22.05 unit checks: the switch, the prompt contract and the coverage note (no database, no model)."""

from types import SimpleNamespace

from app.chat.compare import COMPARE_PROMPT, COMPARE_QUOTA_PROMPT, compare_call, compare_prompt, quota_on
from app.core.settings import Settings
from app.llm.prompts import default_registry
from tests.unit.chat.test_compare import _state


def test_switch_defaults_off() -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    assert s.chat_compare_quota is False
    assert not quota_on(None) and not quota_on(SimpleNamespace(chat_compare_quota=False))
    assert (
        compare_prompt(None) == COMPARE_PROMPT
        and compare_prompt(SimpleNamespace(chat_compare_quota=True)) == COMPARE_QUOTA_PROMPT
    )


def test_flag_off_call_is_unchanged() -> None:
    s = _state(2, dims=("notice",))
    assert compare_call(s) == compare_call(s, SimpleNamespace(chat_compare_quota=False))


def test_prompt_v2_contract() -> None:
    p = default_registry().get("answer_compare", 2)
    assert (p.output, p.role, p.purpose, p.version) == ("segments", "balanced", "generate", 2)
    assert "Evidence by document" in p.body and "markdown table" in p.body
