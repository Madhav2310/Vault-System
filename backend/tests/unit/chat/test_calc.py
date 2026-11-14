"""Typed calculation segments (`app/chat/calc.py`): operands are read by code from the cited sentences
only, code computes with Decimal, a wrong model result is corrected, an unverifiable one is never shown as
verified. FakeProvider only, no network."""

import time
import uuid
from typing import Any

from app.chat.calc import (
    CALC_CORRECTED,
    CALC_OPERAND_UNVERIFIED,
    MAX_OPERANDS,
    calc_call,
    read_operand,
    resolve_calc,
)
from app.chat.evidence import build_evidence
from app.chat.nodes.generate import generate
from app.llm.providers.fake import structured
from tests.support.chat_gen import chunk, fake_gateway

TEXT = (
    "Revenue was $1,200 in 2023. "  # E1.0
    "Revenue was $1,500 in 2024. "  # E1.1
    "Net loss was (340) for the year. "  # E1.2
    "Churn rose to 12.5% in 2024. "  # E1.3
    "Churn was 10% in 2023. "  # E1.4
    "Cash was $4.2 million at year end. "  # E1.5
    "Debt was $1.3 billion at year end. "  # E1.6
    "Headcount was 0 in the pilot. "  # E1.7
    "Amounts in thousands: receivables 2,500 and payables 1,100. "  # E1.8
    "Fees were 2.5 and 3.5 dollars. "  # E1.9
    "There were 15 offices in 2024."  # E1.10
)
EV = build_evidence([chunk(TEXT)])
OWNER = uuid.uuid4()


def op(handle: str, value: str) -> dict[str, str]:
    return {"handle": handle, "value_text": value}


def seg(
    kind: str, operands: list[Any], stated: str = "", unit: str = "", text: str = "model text 999"
) -> dict[str, Any]:
    return {
        "text": text,
        "evidence": [],
        "calc": {"op": kind, "operands": operands, "unit": unit, "stated_result": stated},
    }


def run(*segments: dict[str, Any]) -> tuple[list[str], list[str], Any]:
    res = resolve_calc(list(segments), EV)
    return [s.text for s in res.segments], list(res.reasons), res


def test_corrected() -> None:
    """AC1: the model says $1,600; code reads $1,200 and $1,500 from the cited sentences and adds."""
    texts, reasons, res = run(seg("add", [op("E1.0", "$1,200"), op("E1.1", "$1,500")], "$1,600"))
    assert texts == ["Adding $1,200 and $1,500 gives $2,700."]
    assert CALC_CORRECTED in reasons and CALC_OPERAND_UNVERIFIED not in reasons
    s = res.segments[0]
    # operand sentences are ordinary citations (adjacent ones merge into one range), cut from storage
    assert s.support == "cited" and len(s.references) == 1
    assert (
        s.references[0].cited_text.startswith("Revenue was $1,200") and "$1,500" in s.references[0].cited_text
    )
    assert "999" not in texts[0]  # the model's wording never carries the number


def test_correct_result_is_not_flagged() -> None:
    texts, reasons, _ = run(seg("subtract", [op("E1.1", "$1,500"), op("E1.0", "$1,200")], "$300"))
    assert texts == ["$1,500 minus $1,200 is $300."] and reasons == []


def test_operand_must_be_in_cited_sentence() -> None:
    """The number is elsewhere in the evidence, not in the cited sentence: unverified, plain, uncited."""
    texts, reasons, res = run(
        seg("add", [op("E1.0", "$1,200"), op("E1.1", "$1,200")], "$2,400", text="Total is $2,400.")
    )
    assert texts == ["Total is $2,400."] and reasons == [CALC_OPERAND_UNVERIFIED]
    assert res.segments[0].support == "none" and res.segments[0].references == ()


def test_unknown_handle_unverified() -> None:
    _, reasons, res = run(seg("sum", [op("E9.9", "$1,200")], "$1,200"))
    assert reasons == [CALC_OPERAND_UNVERIFIED] and res.segments[0].support == "none"


def test_parenthesised_negative_and_percent_change() -> None:
    texts, _, _ = run(seg("add", [op("E1.0", "$1,200"), op("E1.2", "(340)")], "860"))
    assert texts == ["Adding $1,200 and (340) gives $860."]
    texts, _, _ = run(seg("percent_change", [op("E1.0", "$1,200"), op("E1.1", "$1,500")], "25%"))
    assert texts == ["The change from $1,200 to $1,500 is 25%."]
    assert read_operand("Net loss was (340).", "340") is not None
    x = read_operand("Net loss was (340).", "340")
    assert x is not None and x.value == -340  # the sentence wraps it in parentheses


def test_percent_operands() -> None:
    texts, reasons, _ = run(seg("subtract", [op("E1.3", "12.5%"), op("E1.4", "10%")], "2.5%"))
    assert texts == ["12.5% minus 10% is 2.5%."] and reasons == []
    _, reasons, _ = run(seg("add", [op("E1.3", "12.5%"), op("E1.0", "$1,200")], "x"))
    assert reasons == [CALC_OPERAND_UNVERIFIED]  # percent mixed with money is not computed


def test_mixed_units_million_and_billion() -> None:
    texts, reasons, _ = run(
        seg("add", [op("E1.5", "$4.2 million"), op("E1.6", "$1.3 billion")], "$1.5 billion")
    )
    assert texts == ["Adding $4.2 million and $1.3 billion gives $1.3 billion."] and reasons == [
        CALC_CORRECTED
    ]  # 1.3042 at stated dp 1
    texts, _, _ = run(seg("add", [op("E1.5", "$4.2 million"), op("E1.6", "$1.3 billion")], "$1.3042 billion"))
    assert texts == ["Adding $4.2 million and $1.3 billion gives $1.3042 billion."]
    texts, _, _ = run(seg("ratio", [op("E1.6", "$1.3 billion"), op("E1.5", "$4.2 million")], "309.52"))
    assert texts == ["$1.3 billion divided by $4.2 million is 309.52."]


def test_scale_from_sentence_header_and_trailing_word() -> None:
    texts, _, _ = run(seg("subtract", [op("E1.8", "2,500"), op("E1.8", "1,100")], "1,400 thousand"))
    assert texts == ["2,500 minus 1,100 is 1,400 thousand."]
    x = read_operand("Cash was 4.2 million at year end.", "4.2")
    assert x is not None and x.value == 4_200_000  # scale read from the words after the number


def test_division_by_zero_unverified() -> None:
    _, reasons, res = run(seg("ratio", [op("E1.0", "$1,200"), op("E1.7", "0")], "0"))
    assert reasons == [CALC_OPERAND_UNVERIFIED] and res.segments[0].support == "none"
    _, reasons, _ = run(seg("percent_change", [op("E1.7", "0"), op("E1.0", "$1,200")], "0"))
    assert reasons == [CALC_OPERAND_UNVERIFIED]


def test_missing_operand_unverified() -> None:
    _, reasons, _ = run(seg("add", [op("E1.0", "$1,200"), op("E1.1", "$9,999")], "$11,199"))
    assert reasons == [CALC_OPERAND_UNVERIFIED]
    _, reasons, _ = run(seg("add", [op("E1.10", "5")], "5"))  # "5" is only inside "15": not an occurrence
    assert reasons == [CALC_OPERAND_UNVERIFIED]


def test_rounding_half_up() -> None:
    texts, _, _ = run(seg("average", [op("E1.9", "2.5"), op("E1.9", "3.5")], "3.0"))
    assert texts == ["The average of 2.5 and 3.5 is 3.0."]
    texts, _, _ = run(
        seg("sum", [op("E1.9", "2.5"), op("E1.9", "3.5")], "6")
    )  # stated precision: whole numbers
    assert texts == ["Adding 2.5 and 3.5 gives 6."]
    texts, reasons, _ = run(seg("average", [op("E1.9", "2.5"), op("E1.9", "3.5")], "3"))
    assert texts == ["The average of 2.5 and 3.5 is 3."] and reasons == []
    texts, reasons, _ = run(
        seg("ratio", [op("E1.9", "2.5"), op("E1.9", "3.5")], "0.71")
    )  # 0.714285.. -> 0.71
    assert texts[0].endswith("is 0.71.") and reasons == []
    texts, _, _ = run(seg("subtract", [op("E1.9", "2.5"), op("E1.9", "3.5")], "-1"))
    assert texts == ["2.5 minus 3.5 is -1."]
    texts, _, _ = run(seg("ratio", [op("E1.9", "2.5"), op("E1.9", "3.5")], "1"))  # 0.714 -> 1 at dp 0
    assert texts == ["2.5 divided by 3.5 is 1."]


def test_half_up_at_a_tie() -> None:
    ev = build_evidence([chunk("Alpha was 5. Beta was 10.")])
    res = resolve_calc([seg("ratio", [op("E1.0", "5"), op("E1.1", "10")], "1", "")], ev)
    assert res.segments[0].text.endswith("is 1.")  # 0.5 -> 1 (half up), matching the stated precision
    assert CALC_CORRECTED not in res.reasons


def test_many_operands_bounded() -> None:
    many = [op("E1.0", "$1,200")] * (MAX_OPERANDS + 60)
    t0 = time.perf_counter()
    texts, reasons, _ = run(seg("sum", many, "$1", text="Big total."))
    assert texts == ["Big total."] and reasons == [CALC_OPERAND_UNVERIFIED]
    assert time.perf_counter() - t0 < 1
    ok = [op("E1.0", "$1,200")] * MAX_OPERANDS
    texts, reasons, _ = run(seg("sum", ok, "$60,000"))
    assert texts[0].endswith("gives $60,000.") and reasons == []


def test_malformed_calc_degrades_to_normal_segment() -> None:
    bad: list[Any] = [
        "not a dict",
        {"op": "power", "operands": [op("E1.0", "$1,200")]},
        {"op": "add", "operands": "x"},
        {"op": "add", "operands": []},
        {"op": "add", "operands": [{"handle": 1, "value_text": 2}]},
        {"op": "add", "operands": [op("E1.0", "$1,200")], "stated_result": None},  # missing fields are fine
    ]
    for calc in bad[:5]:
        res = resolve_calc([{"text": "Plain claim.", "evidence": ["E1.0"], "calc": calc}], EV)
        s = res.segments[0]
        assert (s.text, s.support, res.reasons) == ("Plain claim.", "cited", ())
    res = resolve_calc(
        [
            {
                "text": "t",
                "evidence": ["E1.0"],
                "calc": {
                    "op": "add",
                    "operands": [op("E1.0", "$1,200"), op("E1.1", "$1,500")],
                    "stated_result": None,
                },
            }
        ],
        EV,
    )
    assert res.segments[0].text == "Adding $1,200 and $1,500 gives $2,700."
    res = resolve_calc(
        [{"text": "t", "evidence": ["E1.0"], "calc": {"op": "subtract", "operands": [op("E1.0", "$1,200")]}}],
        EV,
    )
    assert res.reasons == (CALC_OPERAND_UNVERIFIED,)  # wrong operand count: never shown as verified
    res = resolve_calc([{"text": "No calc.", "evidence": ["E1.0"], "calc": None}], EV)
    assert res.segments[0].text == "No calc." and res.reasons == ()


async def test_generate_with_calc_and_malformed_answer() -> None:
    good = structured(
        {
            "status": "answer",
            "segments": [
                {
                    "text": "x",
                    "evidence": ["E1.0"],
                    "calc": {
                        "op": "add",
                        "operands": [op("E1.0", "$1,200"), op("E1.1", "$1,500")],
                        "unit": "",
                        "stated_result": "$1,600",
                    },
                }
            ],
            "followups": [],
        }
    )
    gw, fake = fake_gateway([good])
    res = await generate(gw, owner_id=OWNER, evidence=EV, question="q", route="qa", calc=True)
    assert res.response_type == "answer" and CALC_CORRECTED in res.reasons
    assert res.resolution.segments[0].text == "Adding $1,200 and $1,500 gives $2,700."
    schema = fake.requests[0].output_schema
    assert "Calc" in str(schema) and "stated_result" in str(schema)
    # a schema-invalid calc (unknown op) degrades to the plain answer, never GENERATION_INVALID
    bad = structured(
        {
            "status": "answer",
            "segments": [{"text": "Twelve.", "evidence": ["E1.0"], "calc": {"op": "power", "operands": []}}],
            "followups": [],
        }
    )
    gw, _ = fake_gateway([bad])
    res = await generate(gw, owner_id=OWNER, evidence=EV, question="q", route="qa", calc=True)
    assert res.response_type == "answer" and res.resolution.segments[0].text == "Twelve."


async def test_flag_off_is_unchanged() -> None:
    reply = structured(
        {"status": "answer", "segments": [{"text": "Twelve.", "evidence": ["E1.0"]}], "followups": []}
    )
    gw_a, fake_a = fake_gateway([reply])
    gw_b, fake_b = fake_gateway([reply])
    a = await generate(gw_a, owner_id=OWNER, evidence=EV, question="q", route="qa")
    b = await generate(gw_b, owner_id=OWNER, evidence=EV, question="q", route="qa", calc=False)
    assert a.resolution == b.resolution and a.reasons == b.reasons
    assert fake_a.requests[0].output_schema == fake_b.requests[0].output_schema
    assert "calc" not in str(fake_a.requests[0].output_schema)
    assert fake_a.requests[0].system == fake_b.requests[0].system

    class S:
        chat_calc = False

    class St:
        compare = None

    assert calc_call(St(), S(), False) == {}

    class On:
        chat_calc = True

    assert calc_call(St(), On(), False) == {"prompt": ("answer_rag", 5), "calc": True}
    assert calc_call(St(), On(), True) == {}  # whole-document turns are unchanged
