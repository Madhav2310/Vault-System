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
    "role": "fast",
    "output": "schema",
    "effort": "low",
    "max_tokens": 2048,
}


def test_registry_validation(tmp_path: pathlib.Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    p = parse_prompt(write_prompt(a, "analyze_query@1.md", **GOOD))
    assert (p.id, p.version, p.effort, p.output, p.role, p.max_tokens) == (
        "analyze_query",
        1,
        "low",
        "schema",
        "fast",
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
        "role": ("gpt-6-luna", "role must be"),
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
    assert (ex.output, ex.effort, ex.role) == ("schema", "low", "fast")


def test_role_frontmatter(tmp_path: pathlib.Path) -> None:
    for role in ("fast", "balanced", "deep", "flagship"):
        d = tmp_path / role
        d.mkdir()
        assert parse_prompt(write_prompt(d, "analyze_query@1.md", **{**GOOD, "role": role})).role == role
    for bad in ("turbo", "haiku_model", "claude-haiku-5-5"):
        d = tmp_path / f"bad_{bad}"
        d.mkdir()
        write_prompt(d, "analyze_query@1.md", **{**GOOD, "role": bad})
        with pytest.raises(PromptRegistryError, match="role must be one of"):
            PromptRegistry.load(d)
    d = tmp_path / "old"  # the previous `model:` key is no longer accepted in place of `role`
    d.mkdir()
    write_prompt(d, "analyze_query@1.md", **{**{k: v for k, v in GOOD.items() if k != "role"}, "model": "x"})
    with pytest.raises(PromptRegistryError, match=r"missing front-matter fields \['role'\]"):
        PromptRegistry.load(d)


def test_answer_rag_sections() -> None:
    p = default_registry().get("answer_rag", 12)
    headings = [ln for ln in p.body.splitlines() if ln.startswith("## ")]
    assert [h.split(".")[0] for h in headings] == [f"## {i}" for i in range(1, 9)]
    expected = [
        "Role and ground rules",
        "Citation rule",
        "Format rule by route",
        "Coverage",
        "Conflicts",
        "Completeness, length and style",
        "History",
        "Follow-up questions",
    ]
    assert [h.split(". ", 1)[1] for h in headings] == expected
    assert "untrusted" in p.body and "abstain" in p.body and "never write a markdown table" in p.body.lower()


def test_answer_rag_frontmatter() -> None:
    p = default_registry().get("answer_rag", 12)
    assert (p.output, p.role, p.effort, p.purpose) == ("segments", "balanced", "none", "generate")
    assert p.max_tokens >= 1000


def test_brief_extract_sections() -> None:
    p = default_registry().get("brief_extract", 3)
    assert (p.role, p.output, p.effort, p.purpose, p.max_tokens) == (
        "balanced",
        "schema",
        "low",
        "brief_extract",
        1800,
    )
    headings = [ln for ln in p.body.splitlines() if ln.startswith("## ")]
    assert [h.split(".")[0] for h in headings] == [f"## {i}" for i in range(1, 9)]
    expected = [
        "Ground rules",
        "Untrusted content",
        "Field guide for facts",
        "Summary",
        "Suggested questions",
        "Category and tags",
        "Tone",
        "Output reminder",
    ]
    assert [h.split(". ", 1)[1] for h in headings] == expected


def test_brief_merge_frontmatter() -> None:
    p = default_registry().get("brief_merge", 2)
    assert (p.role, p.output, p.purpose) == ("fast", "schema", "brief_merge")
