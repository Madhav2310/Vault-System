import pathlib
import re

import pytest
from pydantic import SecretStr, ValidationError

from app.core.settings import Settings

ENV_EXAMPLE = pathlib.Path(__file__).resolve().parents[4] / ".env.example"
REQUIRED = {"DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/d", "REDIS_URL": "redis://h:6379/0"}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def make(env: pytest.MonkeyPatch, **extra: str) -> Settings:
    for k, v in {**REQUIRED, **extra}.items():
        env.setenv(k, v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_missing_required_raises_named_error(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("REDIS_URL", REQUIRED["REDIS_URL"])
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "database_url" in str(exc.value)


def test_env_example_lists_all_fields() -> None:
    listed = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M))
    missing = {n.upper() for n in Settings.model_fields} - listed
    assert not missing, f".env.example lacks {sorted(missing)}"


def test_secrets_masked(clean_env: pytest.MonkeyPatch) -> None:
    s = make(clean_env, ANTHROPIC_API_KEY="sk-ant-very-secret")
    assert isinstance(s.anthropic_api_key, SecretStr)
    assert "sk-ant-very-secret" not in repr(s)
    assert "sk-ant-very-secret" not in str(s.model_dump())
    for name, field in Settings.model_fields.items():
        if name.endswith("_api_key"):
            assert "SecretStr" in str(field.annotation), name


def test_model_ids_no_date_suffix(clean_env: pytest.MonkeyPatch) -> None:
    s = make(clean_env)
    for mid in (s.answer_model, s.haiku_model, s.opus_model):
        assert re.fullmatch(r"claude-[a-z]+-\d+-\d+", mid), mid
    assert s.answer_model == "claude-sonnet-5-5"
    assert s.haiku_model == "claude-haiku-5-5"
    assert s.embedding_model == "text-embedding-3-small"
    assert s.embedding_dim == 1536
