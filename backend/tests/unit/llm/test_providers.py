from typing import Any

import anthropic
import pytest

from app.llm.providers.anthropic import OTEL_OPTION, AnthropicProvider


def test_anthropic_otel_metadata_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real = anthropic.AsyncAnthropic

    def spy(*args: Any, **kwargs: Any) -> anthropic.AsyncAnthropic:
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", spy)
    AnthropicProvider(api_key="test-key", timeout_s=5).client  # noqa: B018  (lazy construction)
    assert seen["open_telemetry"] == {"traces": {"content_mode": "metadata_only"}}
    assert seen["open_telemetry"] == OTEL_OPTION
