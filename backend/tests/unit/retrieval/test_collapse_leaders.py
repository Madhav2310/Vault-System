"""retrieval@5: dot leaders are collapsed in the reranker's copy of a passage only."""

from app.retrieval.config import get_config
from app.retrieval.windows import LEADER_MARKER, collapse_leaders, focus_window

DOTS = "." * 90
QUERY = "operating cash flow ratio total current liabilities"


def _balance_sheet() -> tuple[str, list[tuple[int, int]]]:
    rows = [f"Row label {i}{DOTS} {i},000" for i in range(8)]
    rows[0] = f"Row label 0 operating cash flow ratio total current liabilities{DOTS} 0,000"
    rows += ["Total current liabilities" + DOTS + " 1,234,567"]
    text, spans, pos = "", [], 0
    for r in rows:
        if text:
            text += " "
            pos += 1
        spans.append((pos, pos + len(r)))
        text += r
        pos += len(r)
    return text, spans


def test_collapse_off_misses_the_total_row_and_on_keeps_it() -> None:
    text, spans = _balance_sheet()
    off = focus_window(text, spans, QUERY, 180, 400)
    on = focus_window(text, spans, QUERY, 180, 400, collapse=True)
    assert "1,234,567" not in off
    assert "Total current liabilities" in on and "1,234,567" in on and "...." not in on


def test_collapse_off_is_the_default_output() -> None:
    text, spans = _balance_sheet()
    assert focus_window(text, spans, QUERY, 180, 400) == focus_window(
        text, spans, QUERY, 180, 400, collapse=False
    )


def test_spans_stay_valid_after_collapse() -> None:
    text, spans = _balance_sheet()
    new, new_spans = collapse_leaders(text, spans)
    assert len(new_spans) == len(spans)
    assert all(0 <= a <= b <= len(new) for a, b in new_spans)
    last = new[new_spans[-1][0] : new_spans[-1][1]]
    assert last.startswith("Total current liabilities") and last.endswith("1,234,567")
    assert new[new_spans[0][0] : new_spans[0][1]] == (
        f"Row label 0 operating cash flow ratio total current liabilities{LEADER_MARKER}0,000"
    )


def test_dot_space_and_ellipsis_runs_collapse_and_short_runs_stay() -> None:
    text = "Cash . . . . . . 5 and Debt…………… 7 and Mr... and 3.5"
    new, _ = collapse_leaders(text, [(0, len(text))])
    assert new.count("…") == 2
    assert "Mr..." in new and "3.5" in new


def test_original_text_is_not_modified() -> None:
    text, spans = _balance_sheet()
    before = text
    collapse_leaders(text, spans)
    assert text == before


def test_retrieval_5_collapses_and_earlier_versions_do_not() -> None:
    assert get_config("retrieval@5").rerank.collapse_leaders is True
    assert get_config("retrieval@4").rerank.collapse_leaders is False
    assert get_config("retrieval@2").rerank.collapse_leaders is False
    a, b = get_config("retrieval@4"), get_config("retrieval@5")
    assert a.model_dump(exclude={"version", "rerank"}) == b.model_dump(exclude={"version", "rerank"})


def test_collapse_takes_the_number_only_sentences_after_the_label() -> None:
    parts = [
        "Intro text about the balance sheet.",
        "Total current liabilities",
        f"{DOTS}",
        "2,213,556",
        "2,494,435",
    ]
    parts += ["Long-term liabilities:", "Debt obligations", "1,907,231"]
    text, spans = "", []
    for r in parts:
        text += (" " if text else "") + r
        start = len(text) - len(r)
        spans.append((start, len(text)))
    q = "total current liabilities"
    off = focus_window(text, spans, q, 8, 1050)
    on = focus_window(text, spans, q, 8, 1050, collapse=True)
    assert "2,213,556" not in off
    assert "2,213,556" in on and "2,494,435" in on and "1,907,231" not in on


def test_chunk_without_leaders_gets_the_same_window_with_collapse_on() -> None:
    parts = [
        "Intro text about the agreement.",
        "The fee is payable within",
        "30",
        "45",
        "Other terms follow.",
    ]
    parts += ["Unrelated clause about notices and addresses for service."] * 6
    text, spans = "", []
    for r in parts:
        text += (" " if text else "") + r
        spans.append((len(text) - len(r), len(text)))
    q = "fee payable within"
    assert focus_window(text, spans, q, 8, 1050, collapse=True) == focus_window(text, spans, q, 8, 1050)
