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
