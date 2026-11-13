"""Every reason code the app can record has a user template (S18.03, design §10.1)."""

import ast
import pathlib
import re

from app.explain import templates as T

APP = pathlib.Path(__file__).resolve().parents[3] / "app"
# Turn-level codes live in these packages. `app/insights` codes (BRIEF_*, SUMMARY_*) belong to documents and
# never appear in `rag_runs.reason_codes`.
SCANNED = ("chat", "retrieval", "llm")
CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")


def _constants() -> dict[str, list[str]]:
    """module-level `NAME = "VALUE"` constants whose value looks like a reason code, by value."""
    found: dict[str, list[str]] = {}
    for pkg in SCANNED:
        for path in (APP / pkg).rglob("*.py"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and CODE.match(node.value.value)
                    and node.targets[0].id.isupper()
                ):
                    found.setdefault(node.value.value, []).append(
                        f"{path.relative_to(APP)}:{node.targets[0].id}"
                    )
    return found


def test_all_codes_templated() -> None:
    codes = _constants()
    assert len(codes) >= 15  # the scan is not vacuous
    for known in (
        "NO_EVIDENCE",
        "SCOPE_EMPTY",
        "CITATION_UNRESOLVED",
        "TURN_ERROR",
        "CLARIFY_AMBIGUOUS_QUERY",
    ):
        assert known in codes, f"scan missed {known}"
    missing = {c: where for c, where in codes.items() if c not in T.REASON_TEMPLATES}
    assert not missing, f"reason codes without a template in app/explain/templates.py: {missing}"


def test_degraded_values_templated() -> None:
    from app.retrieval.query_embed import DEGRADED_LEXICAL_ONLY
    from app.retrieval.rerank import DEGRADED_NO_RERANK

    assert {DEGRADED_LEXICAL_ONLY, DEGRADED_NO_RERANK} <= set(T.DEGRADED_TEMPLATES)


def test_templates_do_not_leak_internals() -> None:
    texts = [*T.REASON_TEMPLATES.values(), *T.DEGRADED_TEMPLATES.values()]
    for t in texts:
        assert not re.search(r"gpt-|claude|openai|anthropic|prompt|@\d", t, re.I), t


def test_renderers() -> None:
    one = [{"document_title": "Consulting Agreement", "version_no": 1, "pages": [3, 3], "chunks_used": 3}]
    assert T.answered(one) == "Answered from 3 passages in 'Consulting Agreement' v1."
    assert T.answered([]) is None
    two = [*one, {"document_title": "NDA", "version_no": 2, "pages": None, "chunks_used": 1}]
    assert T.answered(two) == (
        "Answered from 4 passages across 2 documents: 'Consulting Agreement' v1, 'NDA' v2."
    )
    assert T.full_context(2, 18400) == "Read the documents in full (2 documents, 18k tokens)."
    assert T.full_context(1, 3000) == "Read the whole document (1 document, 3k tokens)."
    assert T.unsupported(0) is None and T.unsupported(1) == "One sentence is not supported by its citation."
