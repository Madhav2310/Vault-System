"""README: sections in order, every assignment feature mapped to a place and a status, real commands."""

import pathlib
import re
import shlex

ROOT = pathlib.Path(__file__).resolve().parents[4]
README = (ROOT / "README.md").read_text()

HEADINGS = [
    "What it is",
    "Quickstart",
    "The assignment, feature by feature",
    "API quick tour",
    "How it works",
    "Quality, scale and cost at a glance",
    "Testing",
    "Limits",
    "Repo map",
    "Documentation",
]
STATUSES = ("Built", "Not built", "Not used", "Script built")
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


def test_screenshots_are_embedded_and_exist() -> None:
    images = re.findall(r'(?:!\[[^\]]*\]\(|<img src=")(docs/img/ui/[^)"]+)', README)
    assert len(images) >= 4
    for image in images:
        assert (ROOT / image).is_file(), image


def test_every_feature_has_a_status_and_a_place() -> None:
    rows = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in _section("The assignment, feature by feature").splitlines()
        if line.startswith("|") and not re.fullmatch(r"\|[-| :]+\|", line.strip())
    ]
    rows = [r for r in rows if r[0] not in {"Requirement", "Feature", "Deliverable"}]
    assert len(rows) >= 40
    bad = [r[0] for r in rows if not r[1].startswith(STATUSES)]
    assert not bad, f"rows without a recognised status: {bad}"
    unplaced = [r[0] for r in rows if not r[1].startswith("Not built") and "`" not in r[2]]
    assert not unplaced, f"built rows that point nowhere: {unplaced}"
    spec_items = ("Intelligent upload handler", "Ask a question", "Document statistics API", "Streaming chat")
    for spec_item in spec_items:
        assert any(r[0].startswith(spec_item) for r in rows), spec_item


def test_quality_numbers_have_commands() -> None:
    """Every row with a number in the at-a-glance table carries a regenerate command."""
    bad = []
    for line in _section("Quality, scale and cost at a glance").splitlines():
        if not line.startswith("|") or line.startswith("| Measure"):
            continue
        if re.fullmatch(r"\|[-| :]+\|", line.strip()):
            continue
        prose = re.sub(r"`[^`]*`", "", line)
        if re.search(r"\d", prose) and not COMMAND_SPAN.search(line):
            bad.append(line[:80])
    assert not bad, bad
    assert len(re.findall(r"^\|", _section("Quality, scale and cost at a glance"), re.M)) >= 8


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


def test_make_targets_named_in_backticks_exist() -> None:
    targets = _make_targets()
    named = {t for t in re.findall(r"`make ([a-z0-9-]+)", README) if not t.endswith("-")}
    assert not {t for t in named if t not in targets}
