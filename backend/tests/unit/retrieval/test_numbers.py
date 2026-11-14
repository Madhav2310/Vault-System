"""Number and unit variants for lexical queries (pure functions)."""

import uuid

from app.retrieval.lexical import lexical_statement
from app.retrieval.numbers import MAX_PER_NUMBER, MAX_TOTAL, flat_variants, number_variants


def test_billion_to_million_and_bare() -> None:
    (forms,) = number_variants("What was revenue of $1.2 billion?")
    assert forms[0] == "1.2 billion"
    assert {"1,200 million", "1200 million", "1,200", "1200"} <= set(forms)


def test_million_to_billion() -> None:
    (forms,) = number_variants("about 1,200 million in sales")
    assert "1.2 billion" in forms and "1,200" in forms


def test_abbreviations_attached() -> None:
    assert "3.4 billion" in flat_variants("sales of 3.4B")
    assert "45.2 million" in flat_variants("net 45.2 mm")
    assert "3.4 billion" in flat_variants("revenue 3.4bn")


def test_single_letter_scale_needs_attachment() -> None:
    assert flat_variants("a wall 5 m high") == []
    assert flat_variants("the 10 b") == []
    assert "5 million" in flat_variants("$5 M")


def test_thousands_separator_forms() -> None:
    assert flat_variants("total 12,500") == ["12,500", "12500"]
    assert flat_variants("total 12500") == ["12,500", "12500"]


def test_negatives() -> None:
    assert flat_variants("a loss of (1,234)") == ["1,234", "1234", "-1234"]
    assert "1234" in flat_variants("down -1,234")
    assert flat_variants("fell -5.0%") == []  # percent: the bare figure is already a lexeme


def test_percent_has_no_variants() -> None:
    assert flat_variants("margin of 5.3% and 12 percent") == []


def test_ranges_share_the_scale() -> None:
    a, b = number_variants("between $5-10 million")
    assert a[0] == "5 million" and b[0] == "10 million"
    a, b = number_variants("from 5 to 10 million")
    assert a[0] == "5 million" and b[0] == "10 million"


def test_years_are_not_amounts() -> None:
    assert flat_variants("in 2022") == []
    assert flat_variants("2021-2022 results") == []
    assert "1,200" in flat_variants("the 1200 units")  # a non-year four-digit integer is an amount


def test_fiscal_year_forms() -> None:
    assert flat_variants("fiscal 2022") == ["FY22", "FY2022", "fiscal 2022"]
    assert flat_variants("FY22 results") == ["FY22", "FY2022", "fiscal 2022"]
    assert flat_variants("fiscal year 2023") == ["FY23", "FY2023", "fiscal 2023"]
    assert flat_variants("fy 2021") == ["FY21", "FY2021", "fiscal 2021"]


def test_identifiers_not_mangled() -> None:
    for q in [
        "Form 10-K",
        "10-K for 2022",
        "clause 4.2(b)",
        "INV-2024-001",
        "Q3-2022",
        "ISO 9001-2015",
        "A1B2",
    ]:
        assert flat_variants(q) == [], q


def test_unicode_digits() -> None:
    assert "1200 million" in flat_variants("\uff11\uff12\uff10\uff10 million")  # full-width folds to ASCII
    assert flat_variants("\u0663\u0664 million") == []  # other scripts: no variants, no error
    assert flat_variants("") == []


def test_caps() -> None:
    q = " ".join(f"${i},500 million" for i in range(2, 30))
    groups = number_variants(q)
    assert all(len(g) <= MAX_PER_NUMBER for g in groups)
    assert sum(len(g) for g in groups) <= MAX_TOTAL


def test_forms_are_plain_text() -> None:
    for f in flat_variants("$1.2 billion, (3,000), fiscal 2022, $5-10 million"):
        assert all(ch.isalnum() or ch in " .,-" for ch in f), f


def test_v1_statement_unchanged_and_v2_binds_variants() -> None:
    o, v = uuid.uuid4(), [uuid.uuid4()]
    sql1, p1 = lexical_statement(o, v, "net revenue of $1.2 billion", 5)
    assert "phraseto_tsquery" not in sql1 and not any(k.startswith("nv") for k in p1)
    assert lexical_statement(o, v, "net revenue of $1.2 billion", 5, "v1") == (sql1, p1)
    sql2, p2 = lexical_statement(o, v, "net revenue of $1.2 billion", 5, "v2")
    assert "phraseto_tsquery" in sql2 and p2["nv0_0"] == "1.2 billion"
    assert "1.2 billion" not in sql2  # forms are bound parameters, never interpolated
