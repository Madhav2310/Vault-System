"""S20.01: the final README has its 12 sections in order, sourced numbers, no secrets and real commands."""

import pathlib
import re
import shlex

ROOT = pathlib.Path(__file__).resolve().parents[4]
README = (ROOT / "README.md").read_text()

HEADINGS = [
    "What it is",
    "Quickstart",
    "What works today",
    "Feature tour",
    "Architecture",
    "Key design decisions and assumptions",
    "Evaluation",
    "Scale",
    "Cost",
    "Testing",
    "Limitations and deliberately not built",
    "Repo map",
]
COMMAND_SPAN = re.compile(r"`(make|cd|python3?|scripts/|uv)\b[^`]*`")
# commands a bash block may use that are not make targets or repo scripts
ALLOWED = {"open", "curl", "jq", "docker", "export", "cd", "cp", "echo", "uv", "python3", "python"}


def _section(name: str) -> str:
    m = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", README, re.M | re.S)
    assert m, f"missing section {name}"
    return m.group(1)


def test_sections_in_order() -> None:
    assert README.startswith("# Vault")
    assert re.findall(r"^## (.+)$", README, re.M) == HEADINGS


def test_eval_numbers_have_commands() -> None:
    """Every line with a number in Evaluation and Cost carries a regenerate command (pending rows too)."""
    bad = []
    for name in ("Evaluation", "Cost"):
        for line in _section(name).splitlines():
            if re.fullmatch(r"\|[-| :]+\|", line.strip()):
                continue
            if line.startswith("| Measure") or line.startswith("| Item"):
                continue
            prose = re.sub(r"`[^`]*`", "", line)  # paths and commands do not count as claims
            if re.search(r"\d", prose) and not COMMAND_SPAN.search(line):
                bad.append(f"{name}: {line[:80]}")
    assert not bad, bad
    # the sections do contain tables of numbers (guards against an empty pass)
    assert len(re.findall(r"^\|", _section("Evaluation"), re.M)) >= 10
    assert len(re.findall(r"^\|", _section("Cost"), re.M)) >= 5


def test_no_secrets() -> None:
    assert not re.search(r"sk-[A-Za-z0-9_-]{4,}", README)
    assert not re.search(r"(?i)api[_-]?key\s*=\s*(?!paste-your-key-here)\S{16,}", README)


def _make_targets() -> set[str]:
    return set(re.findall(r"^([A-Za-z0-9_.-]+):", (ROOT / "Makefile").read_text(), re.M))


def _bash_commands() -> list[str]:
    cmds = []
    for block in re.findall(r"```bash\n(.*?)```", README, re.S):
        for line in block.replace("\\\n", " ").splitlines():
            line = line.split("  #")[0].strip()
            if line and not line.startswith("#"):
                cmds.append(line)
    return cmds


def test_commands_exist() -> None:
    targets = _make_targets()
    cmds = _bash_commands()
    assert any(c.startswith("scripts/quickstart.sh") for c in cmds)
    assert any(c.startswith("scripts/demo.sh") for c in cmds)
    bad = []
    for line in cmds:
        if re.match(r"^[A-Za-z_]+=", line):  # a variable assignment (`KEY=...`, `SID=$(curl ...)`)
            continue
        for seg in re.split(r"&&|;", line):
            words = shlex.split(seg.strip())
            while words and re.match(r"^[A-Za-z_]+=", words[0]):
                words.pop(0)
            if not words:
                continue
            cmd = words[0]
            if cmd == "make":
                named = [t for t in words[1:] if not t.startswith("-") and "=" not in t]
                bad += [f"make {t}" for t in named if t not in targets]
            elif "/" in cmd and cmd.endswith((".sh", ".py")):
                if not (ROOT / cmd.removeprefix("./")).is_file():
                    bad.append(cmd)
            elif cmd not in ALLOWED:
                bad.append(cmd)
    assert not bad, bad


def test_linked_files_exist() -> None:
    """Every repo path in backticks under a known top-level directory exists (globs are not matched)."""
    missing = []
    pattern = r"`((?:docs|backend|eval|scripts|frontend|reranker|ops)/[A-Za-z0-9_@./-]+)`"
    for path in set(re.findall(pattern, README)):
        if not (ROOT / path).exists():
            missing.append(path)
    assert not missing, missing
