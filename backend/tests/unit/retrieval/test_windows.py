"""Query-focused rerank windows."""

import random
import re

from app.retrieval.windows import focus_window


def _chunk(sentences: list[str]) -> tuple[str, list[tuple[int, int]]]:
    text, spans, pos = "", [], 0
    for s in sentences:
        if text:
            text += " "
            pos += 1
        spans.append((pos, pos + len(s)))
        text += s
        pos += len(s)
    return text, spans


FILLER = "The parties agree that standard operational matters apply across all regions and periods."


def test_window_contains_best_sentence() -> None:
    sents = (
        [FILLER] * 20
        + ["Either party may terminate this agreement on thirty days written notice."]
        + [FILLER] * 20
    )
    text, spans = _chunk(sents)
    assert len(text.split()) > 400
    w = focus_window(text, spans, "How much notice to terminate the agreement?", 180)
    assert "thirty days written notice" in w and len(w.split()) <= 180


def test_no_query_match_falls_back_to_the_first_words() -> None:
    text, spans = _chunk([FILLER] * 40)
    w = focus_window(text, spans, "zebra quantum", 180, max_chars=10_000)
    assert w == " ".join(text.split()[:180])


def test_long_sentence_cut() -> None:
    long = " ".join(["notice"] * 400) + "."
    w = focus_window(long, [(0, len(long))], "notice", 180, max_chars=10_000)
    assert len(w.split()) == 180


def test_whole_sentences_in_order() -> None:
    rng = random.Random(3)
    vocab = ["alpha", "beta", "gamma", "delta", "notice", "term", "fee", "payment", "audit"]
    for _ in range(50):
        sents = [" ".join(rng.choices(vocab, k=rng.randint(5, 30))) + "." for _ in range(rng.randint(10, 40))]
        text, spans = _chunk(sents)
        w = focus_window(text, spans, "notice term audit", 120, max_chars=10_000)
        assert len(w.split()) <= 120
        if len(text.split()) <= 120:
            assert w == text
            continue
        pieces = [s for s in sents if s in w]
        assert pieces, "window must contain at least one whole sentence"
        # consecutive and in original order
        idx = [sents.index(p) for p in pieces]
        assert idx == list(range(idx[0], idx[0] + len(idx)))
        assert re.sub(r"\s+", " ", w) == " ".join(pieces)


def test_char_cap_applies() -> None:
    text, spans = _chunk([FILLER] * 40)
    assert len(focus_window(text, spans, "operational", 180)) <= 1050
