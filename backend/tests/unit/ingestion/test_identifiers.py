"""Identifier-token extraction: the same tokens `lexical.identifier_query` makes of a query."""

from app.ingestion.identifiers import extract_identifier_tokens, tsv_ids_source
from app.retrieval.lexical import identifier_query

FIXTURE = (
    "Per Section 7.3 and clause 4.2(b), the fee is $1,250,000 (12.5%) due 2024-03-15, see EX-10.1 and "
    "INV-2024-001. The CLOSING DATE is 3/15/2024. Page 7. Repeat: Section 7.3."
)


def test_extract_fixture() -> None:
    assert extract_identifier_tokens(FIXTURE) == [
        "7.3",
        "4.2(b)",
        "$1,250,000",
        "12.5",
        "2024-03-15",
        "10.1",
        "2024-001",
        "3/15/2024",
        "7",
    ]


def test_matches_query_side_tokenisation() -> None:
    """Index side and query side must agree, or the identifier never matches."""
    assert " ".join(extract_identifier_tokens(FIXTURE)) == " ".join(
        dict.fromkeys(identifier_query(FIXTURE).split())
    )


def test_no_identifiers() -> None:
    assert extract_identifier_tokens("The supplier shall deliver goods promptly.") == []
    assert tsv_ids_source("The supplier shall deliver goods promptly.") is None
    assert tsv_ids_source("See Section 7.3.") == "7.3"
