"""The documentation set is self-consistent: links and paths resolve, with no plans or task ids."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[4]
DOCS = [
    ROOT / "README.md",
    ROOT / "ARCHITECTURE.md",
    ROOT / "CLAUDE.md",
    *sorted((ROOT / "docs").glob("*.md")),
]

TOP_DIRS = ("docs", "backend", "eval", "scripts", "frontend", "reranker", "ops")
ROOT_FILES = ("README.md", "ARCHITECTURE.md", "CLAUDE.md", "AI_USAGE.md", "Makefile", ".env.example")
# paths that are produced by running a command or fetched from third parties, so they are not committed
GENERATED_PREFIXES = ("eval/sources/", "eval/reports/", "eval/baselines/answer_v2.json")
PATH_SPAN = re.compile(r"`([^`\s]+)`")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
BANNED = re.compile(
    r"S[0-9]{1,2}\.[0-9]{2}|docs/plan|docs/design|docs/research|docs/status|docs/evidence"
    r"|design §|\bv3\b|S1[6-9]\b|S2[0-3]\b"
)


def _slug(heading: str) -> str:
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", text)


def _anchors(path: pathlib.Path) -> set[str]:
    return {_slug(h) for h in re.findall(r"^#{1,6} (.+)$", path.read_text(), re.M)}


def _repo_paths(text: str) -> set[str]:
    found = set()
    for span in PATH_SPAN.findall(text):
        span = span.split("::")[0].rstrip(".,;:)")
        if any(c in span for c in "*<>{}$=|") or "..." in span:
            continue
        in_repo = span.startswith(tuple(f"{d}/" for d in TOP_DIRS)) or span in ROOT_FILES
        if in_repo and not span.startswith(GENERATED_PREFIXES):
            found.add(span)
    return found


def test_documentation_set_is_the_expected_one() -> None:
    names = sorted(p.name for p in (ROOT / "docs").glob("*.md"))
    assert names == ["README.md", "evaluation.md", "operations.md", "voice.md"]
    assert sorted(p.name for p in (ROOT / "docs").iterdir() if p.is_dir()) == ["api", "img", "spec"]


def test_relative_links_resolve() -> None:
    bad = []
    for doc in DOCS:
        for target in LINK.findall(doc.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (doc.parent / file_part).resolve() if file_part else doc
            if not dest.exists():
                bad.append(f"{doc.name}: {target}")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                bad.append(f"{doc.name}: {target} (no such heading)")
    assert not bad, bad


def test_backticked_repo_paths_exist() -> None:
    bad = []
    for doc in DOCS:
        for path in sorted(_repo_paths(doc.read_text())):
            if not (ROOT / path).exists():
                bad.append(f"{doc.name}: {path}")
    assert not bad, bad


def test_docs_describe_the_present_only() -> None:
    bad = []
    for doc in DOCS:
        for n, line in enumerate(doc.read_text().splitlines(), 1):
            if BANNED.search(line):
                bad.append(f"{doc.name}:{n}: {line[:80]}")
    assert not bad, bad
