"""Prompt contamination check: no prompt the system or the judges use by default may share wording with the
evaluation set.

A prompt is contaminated when it shares a word 5-gram with any gold item's question, required or optional
claims, or accepted variants, in any split, or when it uses a proper name (a capitalised word inside a
sentence) that the gold items use, such as a party or document name. Matches are reported by prompt and item
id only; test-split text is never printed. Generic phrasing that recurs across many items (CUAD's template
questions, the abstention template) is listed in `ALLOWED`, generic capitalised words in `GENERIC_NAMES`.

    python -m eval.contamination            # the default prompts; exit 1 on a match
    python -m eval.contamination --all      # every prompt version (older versions carry known matches)
"""

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

N = 5
_WORD = re.compile(r"[a-z0-9]+(?:['\u2019][a-z]+)?")
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "backend" / "prompts"

# Shared phrasing that names no answer: CUAD's category questions and the abstention template.
ALLOWED: frozenset[str] = frozenset(
    {
        "is there a source code",
        "there a source code escrow",
        "who are the parties to",
        "the documents provided do not",
        "documents provided do not state",
    }
)


# Capitalised words the evaluation set shares with ordinary prompt language: no party or document name.
GENERIC_NAMES: frozenset[str] = frozenset(
    {
        "Agreement",
        "Customer",
        "Supplier",
        "Section",
        "Company",
        "Inc",
        "Delaware",
        "March",
        "January",
        "February",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "English",
    }
)
_CAP = re.compile(r"\b([A-Z][a-z]{2,})\b")
_SENTENCE_START = re.compile(r"(^|[.!?:\n\"(\[*#-]\s?)$")


def names(text: str) -> set[str]:
    """Capitalised words that do not start a sentence, a quote or a list item."""
    return {
        m.group(1)
        for m in _CAP.finditer(text)
        if not _SENTENCE_START.search(text[max(0, m.start() - 2) : m.start()])
    }


def grams(text: str, n: int = N) -> set[str]:
    words = _WORD.findall(text.lower())
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def default_prompts() -> list[tuple[str, int]]:
    """Every prompt a default turn or a default judge run calls."""
    from app.chat.compare import COMPARE_PROMPT
    from app.chat.nodes.analyze import PROMPT as ANALYZE
    from app.chat.nodes.generate import PROMPT as ANSWER
    from app.chat.support_check import PROMPT as SUPPORT_CHECK
    from app.retrieval.listwise import PROMPT as RERANK
    from eval.judge import PROMPTS_V2

    return sorted({ANALYZE, ANSWER, COMPARE_PROMPT, SUPPORT_CHECK, RERANK, *PROMPTS_V2.values()})


def item_index(items: Iterable[object]) -> dict[str, set[tuple[str, str]]]:
    """5-gram -> {(split, item id)} over each item's question, claims and accepted variants."""
    from eval.splits_v2 import split_of

    idx: dict[str, set[tuple[str, str]]] = {}
    for it in items:
        alts = getattr(it, "alternates", None) or {}
        texts = [getattr(it, "question", "") or ""]
        texts += list(getattr(it, "required_claims", None) or []) + list(
            getattr(it, "optional_claims", None) or []
        )
        texts += [v for vs in alts.values() for v in vs] if isinstance(alts, dict) else []
        for t in texts:
            for g in grams(t):
                idx.setdefault(g, set()).add((split_of(it.source_doc), it.id))  # type: ignore[attr-defined]
    return idx


def name_index(items: Iterable[object]) -> dict[str, set[tuple[str, str]]]:
    """proper name -> {(split, item id)} over each item's question and claims."""
    from eval.splits_v2 import split_of

    idx: dict[str, set[tuple[str, str]]] = {}
    for it in items:
        texts = [getattr(it, "question", "") or "", *(getattr(it, "required_claims", None) or [])]
        for t in texts:
            for w in names(t) - GENERIC_NAMES:
                idx.setdefault(w, set()).add((split_of(it.source_doc), it.id))  # type: ignore[attr-defined]
    return idx


def matches(prompt_text: str, idx: dict[str, set[tuple[str, str]]]) -> dict[tuple[str, str], list[str]]:
    """(split, item id) -> the shared 5-grams, `ALLOWED` excluded."""
    out: dict[tuple[str, str], list[str]] = {}
    for g in sorted(grams(prompt_text) - ALLOWED):
        for key in idx.get(g, ()):
            out.setdefault(key, []).append(g)
    return out


def name_matches(prompt_text: str, idx: dict[str, set[tuple[str, str]]]) -> dict[tuple[str, str], list[str]]:
    """(split, item id) -> the proper names the prompt shares with that item."""
    out: dict[tuple[str, str], list[str]] = {}
    for w in sorted(names(prompt_text) - GENERIC_NAMES):
        for key in idx.get(w, ()):
            out.setdefault(key, []).append(w)
    return out


def findings(files: Sequence[Path]) -> list[tuple[str, str, str, str, list[str]]]:
    """(prompt file, kind, split, item id, shared text) for every match."""
    from eval.loader import load_dataset_v2

    items = load_dataset_v2().items
    gidx, nidx = item_index(items), name_index(items)
    out = []
    for f in files:
        text = f.read_text("utf-8")
        for kind, found in (("5-gram", matches(text, gidx)), ("name", name_matches(text, nidx))):
            out += [(f.name, kind, sp, iid, shared) for (sp, iid), shared in sorted(found.items())]
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval.contamination")
    ap.add_argument("--all", action="store_true", help="check every prompt file, not only the defaults")
    a = ap.parse_args(argv)
    files = (
        sorted(PROMPTS_DIR.glob("*.md"))
        if a.all
        else [PROMPTS_DIR / f"{pid}@{v}.md" for pid, v in default_prompts()]
    )
    found = findings(files)
    for name, kind, split, iid, shared in found:
        shown = "" if split == "test" else f"  e.g. {shared[0]!r}"
        print(f"{name:32s} {kind:6s} {split:5s} {iid}{shown}")
    print(f"{len(files)} prompts checked, {len(found)} item matches")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
