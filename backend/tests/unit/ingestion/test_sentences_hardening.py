# ruff: noqa: RUF001
"""Regression tests for review findings 3 (quadratic splitter) and 7 (CJK text)."""

import time

from app.ingestion.sentences import split_sentences


def test_abbreviation_runs_are_linear() -> None:
    # "Dr." / "Mr." never end a sentence, so `start` never advances: this was O(n^2)
    # (224 KB took over a minute)
    text = "Dr. Smith saw Mr. Jones and " * 8000  # ~224 KB
    t0 = time.perf_counter()
    spans = split_sentences(text)
    assert time.perf_counter() - t0 < 2.0
    assert len(spans) == 1 and spans[0][0] == 0


def test_cjk_terminators_split_without_spaces() -> None:
    text = "合同自签署之日起生效。任何一方可提前三十日书面通知终止。违约方应赔偿损失！"
    spans = split_sentences(text)
    assert [text[a:b][-1] for a, b in spans] == ["。", "。", "！"]
    assert "".join(text[a:b] for a, b in spans) == text  # a partition: nothing lost


def test_latin_text_behaviour_unchanged() -> None:
    text = "Section 4.2(b) applies. The Supplier shall deliver the Goods. See No. 12 for details."
    assert [text[a:b] for a, b in split_sentences(text)] == [
        "Section 4.2(b) applies.",
        "The Supplier shall deliver the Goods.",
        "See No. 12 for details.",
    ]
