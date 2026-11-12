"""Architecture rules enforced by AST scan (CLAUDE.md: every model call goes through app.llm.gateway)."""

import ast
import pathlib
import re

BACKEND = pathlib.Path(__file__).resolve().parents[2]
PROVIDER_MODULES = {"anthropic", "openai"}
CLIENT_NAMES = {
    "Anthropic",
    "AsyncAnthropic",
    "AnthropicBedrock",
    "AsyncAnthropicBedrock",
    "AnthropicBedrockMantle",
    "AnthropicVertex",
    "AsyncAnthropicVertex",
    "AnthropicFoundry",
    "OpenAI",
    "AsyncOpenAI",
    "AzureOpenAI",
    "AsyncAzureOpenAI",
}
ALLOWED = BACKEND / "app" / "llm"


def violations(source: str, where: str) -> list[str]:
    """Imports of provider SDKs and constructions of their clients, by line."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [
                f"{where}:{node.lineno} import {a.name}"
                for a in node.names
                if a.name.split(".")[0] in PROVIDER_MODULES
            ]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] in PROVIDER_MODULES:
                found.append(f"{where}:{node.lineno} from {node.module} import ...")
        elif isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in CLIENT_NAMES:
                found.append(f"{where}:{node.lineno} {name}(...)")
    return found


def test_no_provider_clients_outside_llm() -> None:
    # the scanner is not vacuous
    assert len(violations("import anthropic\nc = anthropic.AsyncAnthropic()\n", "x")) == 2
    assert violations("from openai import AsyncOpenAI\nAsyncOpenAI()\n", "x") == [
        "x:1 from openai import ...",
        "x:2 AsyncOpenAI(...)",
    ]
    assert violations("from app.llm.gateway import get_gateway\n", "x") == []

    roots = [BACKEND / "app", BACKEND / "scripts", BACKEND.parent / "scripts", BACKEND.parent / "eval"]
    files = [p for r in roots if r.exists() for p in r.rglob("*.py") if ALLOWED not in p.parents]
    assert len(files) > 10
    bad = [
        v
        for p in files
        for v in violations(p.read_text(encoding="utf-8"), str(p.relative_to(BACKEND.parent)))
    ]
    assert not bad, "provider SDK used outside app/llm/:\n" + "\n".join(bad)

    # inside app/llm, only the provider adapters construct clients
    inside = [p for p in ALLOWED.rglob("*.py") if p.parent.name != "providers"]
    bad_inside = [
        v for p in inside for v in violations(p.read_text(encoding="utf-8"), p.name) if "(...)" in v
    ]
    assert not bad_inside, bad_inside


MODEL_ID = re.compile(
    r"\b(?:gpt-[0-9][\w.-]*|o[0-9]-?[\w-]*|claude-[a-z]+-[0-9][\w.-]*|text-embedding-[\w-]+)\b"
)
MODEL_ID_ALLOWED = {"app/llm/routing.py", "app/llm/pricing.py", "app/core/settings.py"}


def test_no_hardcoded_model_ids() -> None:
    """Model IDs appear only in the router, price table and settings (CLAUDE.md: model choice is code)."""
    assert MODEL_ID.search("m = 'gpt-6.1-terra'") and MODEL_ID.search('"claude-sonnet-5-5"')
    assert not MODEL_ID.search("tokens = input_tokens / output")
    app = BACKEND / "app"
    bad = []
    for path in app.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel in MODEL_ID_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
        for node in ast.walk(tree):
            if id(node) in docstrings:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and MODEL_ID.search(node.value):
                bad.append(f"{rel}:{node.lineno} {node.value[:40]!r}")
    assert not bad, "model IDs named outside the router/settings:\n" + "\n".join(bad)
