"""Sentence segmenter (S1.21, design 6.5). Fixture cases were written before the implementation."""

from pathlib import Path

import pytest
import yaml

from app.ingestion.sentences import split_sentences

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sentences.yaml"


def _cases() -> list[dict[str, object]]:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def test_fixture_accuracy() -> None:
    cases = _cases()
    assert len(cases) == 50
    failures: list[str] = []
    for case in cases:
        text = str(case["text"])
        got = [text[s:e] for s, e in split_sentences(text)]
        if got != case["sentences"]:
            failures.append(f"{case['id']}: got {got!r}, expected {case['sentences']!r}")
    score = len(cases) - len(failures)
    assert score >= 48, f"{score}/50\n" + "\n".join(failures)


def test_offsets_partition() -> None:
    texts = [str(c["text"]) for c in _cases()]
    texts.append("A table.\n\n| a | b |\n| 1 | 2 |\n\nTail sentence.  Another one.")
    for text in texts:
        for kind in ("text", "table"):
            spans = split_sentences(text, kind=kind)  # type: ignore[arg-type]
            pos = 0
            for start, end in spans:
                assert pos <= start < end <= len(text)
                assert text[pos:start].strip() == ""
                assert text[start:end] == text[start:end].strip()
                pos = end
            assert text[pos:].strip() == ""


def test_clause_numbers() -> None:
    text = "Section 4.2(b) applies."
    assert split_sentences(text) == [(0, len(text))]


def test_table_rows_are_sentences() -> None:
    text = "| Item | Amount. |\n| Rent | 1.5 |\n\n| Tax | 2.0 |\n"
    got = [text[s:e] for s, e in split_sentences(text, kind="table")]
    assert got == ["| Item | Amount. |", "| Rent | 1.5 |", "| Tax | 2.0 |"]


@pytest.mark.parametrize("text", ["", "  ", "\n\n"])
def test_blank_text(text: str) -> None:
    assert split_sentences(text) == []
    assert split_sentences(text, kind="table") == []


def test_single_newline_does_not_split_but_blank_line_does() -> None:
    assert len(split_sentences("one line\nstill the same")) == 1
    assert len(split_sentences("one line\n \nnew paragraph")) == 2
