import pathlib

import pytest

from app.llm.prompts import (
    REQUIRED_FIELDS,
    PromptRegistry,
    PromptRegistryError,
    default_registry,
    parse_prompt,
)
from tests.support.llm import write_prompt

GOOD = {
    "id": "analyze_query",
    "version": 1,
    "purpose": "analyze_query",
    "model": "haiku_model",
    "output": "schema",
    "effort": "low",
    "max_tokens": 2048,
}


def test_registry_validation(tmp_path: pathlib.Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    p = parse_prompt(write_prompt(a, "analyze_query@1.md", **GOOD))
    assert (p.id, p.version, p.effort, p.output, p.model_key, p.max_tokens) == (
        "analyze_query",
        1,
        "low",
        "schema",
        "haiku_model",
        2048,
    )
    assert len(p.digest) == 64

    # duplicate id@version across directories, and via register()
    write_prompt(b, "analyze_query@1.md", **GOOD)
    with pytest.raises(PromptRegistryError, match=r"duplicate prompt analyze_query@1"):
        PromptRegistry.load(a, b)
    reg = PromptRegistry([p])
    with pytest.raises(PromptRegistryError, match="duplicate"):
        reg.register(p)

    # every required field is enforced
    for field in REQUIRED_FIELDS:
        bad = tmp_path / f"missing_{field}"
        bad.mkdir()
        write_prompt(bad, "analyze_query@1.md", **{k: v for k, v in GOOD.items() if k != field})
        with pytest.raises(PromptRegistryError, match=rf"missing front-matter fields \['{field}'\]"):
            PromptRegistry.load(bad)

    # bad values
    cases = {
        "effort": ("extreme", "effort must be"),
        "output": ("markdown", "output must be"),
        "model": ("claude-haiku-5-5", "model must be"),
        "version": ("one", "must be integers"),
    }
    for field, (value, msg) in cases.items():
        d = tmp_path / f"bad_{field}"
        d.mkdir()
        write_prompt(d, "analyze_query@1.md", **{**GOOD, field: value})
        with pytest.raises(PromptRegistryError, match=msg):
            PromptRegistry.load(d)

    # file name must match id@version; front-matter block required
    d = tmp_path / "mismatch"
    d.mkdir()
    write_prompt(d, "analyze_query@2.md", **GOOD)
    with pytest.raises(PromptRegistryError, match="file name"):
        PromptRegistry.load(d)
    (d / "analyze_query@2.md").write_text("no front matter\n")
    with pytest.raises(PromptRegistryError, match="missing front-matter block"):
        PromptRegistry.load(d)

    with pytest.raises(PromptRegistryError, match="unknown prompt"):
        reg.get("analyze_query", 9)


def test_repo_prompts_load() -> None:
    reg = default_registry()
    ex = reg.get("_example", 1)
    assert (ex.output, ex.effort, ex.model_key) == ("schema", "low", "haiku_model")
