import pytest
from pydantic import ValidationError

from app.core.settings import Settings

URLS = {"database_url": "postgresql+asyncpg://x/y", "redis_url": "redis://x"}


def test_prod_rejects_fake() -> None:
    with pytest.raises(ValidationError, match="llm_provider=fake"):
        Settings(_env_file=None, environment="prod", llm_provider="fake", **URLS)  # type: ignore[call-arg]


def test_fake_allowed_outside_prod() -> None:
    for env in ("dev", "test"):
        s = Settings(_env_file=None, environment=env, llm_provider="fake", **URLS)  # type: ignore[call-arg]
        assert s.llm_provider == "fake"
    assert Settings(_env_file=None, environment="prod", **URLS).llm_provider == "openai"  # type: ignore[call-arg]
