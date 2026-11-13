import uuid
from dataclasses import dataclass

from app.insights.verify import (
    NO_CITATION,
    VALUE_NOT_IN_CITATION,
    Verdict,
    amounts_in,
    dates_in,
    verify_fact,
)

V = uuid.uuid4()


@dataclass(frozen=True)
class Ref:
    cited_text: str
    version_id: uuid.UUID = V


def check(field: str, value: str, *texts: str) -> Verdict:
    return verify_fact(field, value, [Ref(t) for t in texts], V)


def ok(field: str, value: str, *texts: str) -> bool:
    return check(field, value, *texts).status == "verified"


def test_dates() -> None:
    text = "This Agreement is effective as of June 1, 2020."
    assert check("effective_date", "2020-06-01", text) == Verdict("verified", None)
    assert check("effective_date", "2021-06-01", text) == Verdict("unverified", VALUE_NOT_IN_CITATION)
    # any common format in the cited text
    for written in (
        "2020-06-01",
        "June 1, 2020",
        "Jun. 1st, 2020",
        "1 June 2020",
        "the 1st day of June, 2020",
        "06/01/2020",
        "1/6/2020",  # day-first reading of an ambiguous numeric date
    ):
        assert ok("effective_date", "2020-06-01", f"Signed on {written}."), written
    assert ok("expiry_date", "June 1, 2020", "Expires 2020-06-01.")  # the value may be written any way too
    assert not ok("expiry_date", "2020-06-01", "Expires June 2020.")  # a month alone is not that day
    assert not ok("deadline", "2020-02-30", "Due February 30, 2020.")  # impossible dates never match
    assert dates_in("May 5, 2021 and 2021-05-06") == dates_in("5 May 2021, 6 May 2021")
    # a value that is not a date falls back to token overlap
    assert ok("deadline", "30 days after signing", "Payment is due 30 days after signing.")
    assert not ok("deadline", "thirty business days", "Payment is due within the notice period.")


def test_amounts() -> None:
    assert ok("amount", "1250000 USD", "The fee is $1,250,000 payable in installments.")
    assert ok("amount", "$1,250,000", "The fee is 1250000 USD.")
    assert not ok("amount", "1250000", "The fee is $125,000.")
    assert not ok("amount", "$125,000", "The fee is $1,250,000.")  # a substring of the digits is not a match
    assert not ok("amount", "$125,000", "The fee is 1250000")
    # scale words and percent
    assert ok("amount", "$1.25 million", "Total consideration of $1,250,000.")
    assert ok("amount", "5%", "Interest accrues at five percent, being 5 percent per annum.")
    assert ok("amount", "5%", "a rate of 5.0% per annum")
    assert not ok("amount", "5%", "a term of 5 years")  # a bare 5 is not 5 percent
    assert not ok("amount", "€1,000", "The price is $1,000.")  # different currency
    assert [a.number for a in amounts_in("USD 2,000 and 3 million")] == [2000, 3_000_000]
    # no number in the value: token overlap
    assert ok("amount", "monthly retainer fee", "The monthly retainer fee is payable in advance.")


def test_names() -> None:
    cite = "between Medalist Diversified REIT and Acme Consulting LLC."
    assert ok("party", "Medalist Diversified REIT, Inc.", cite)
    assert ok("party", "medalist diversified reit", cite)
    assert ok("party", "ACME CONSULTING", cite)
    assert not ok("party", "Globex Holdings", cite)
    # containment boundary: 4 of 5 tokens = 0.8 verified, 3 of 4 = 0.75 not
    assert ok("party", "alpha beta gamma delta epsilon", "alpha beta gamma delta")
    assert not ok("party", "alpha beta gamma delta", "alpha beta gamma")
    assert ok("governing_law", "State of Delaware", "governed by the laws of the State of Delaware")
    assert not ok("governing_law", "State of New York", "governed by the laws of the State of Delaware")


def test_no_citation() -> None:
    assert check("party", "Acme") == Verdict("unverified", NO_CITATION)
    # a citation into another version does not count
    other = Ref("Acme Consulting", uuid.uuid4())
    assert verify_fact("party", "Acme", [other], V) == Verdict("unverified", NO_CITATION)
    assert verify_fact("party", "Acme", [other, Ref("Acme")], V).status == "verified"


def test_overlap_boundary() -> None:
    def value(n: int) -> str:
        return " ".join(f"w{i:03d}" for i in range(n))

    # 1 of 2 content tokens = 0.5 verified
    assert ok("obligation", "alpha1 beta1", "the alpha1 only")
    # 49 of 100 = 0.49 not verified, 50 of 100 verified
    assert not ok("obligation", value(100), value(49))
    assert ok("obligation", value(100), value(50))
    # stopwords do not count either side
    assert ok("risk", "the penalty is a fine", "a penalty applies, fine")
    assert check("risk", "totally unrelated words", "penalty applies").reason == VALUE_NOT_IN_CITATION
    assert not ok("key_point", "the of and", "the of and")  # no content tokens: nothing to verify
    assert ok("termination", "Either party may terminate", "Either party terminated the contract")
