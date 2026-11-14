"""Meta op validation: allowlist, parameter checks, and the analyze fallback to qa."""

import datetime as dt
import uuid
from typing import Any

import pytest

from app.chat.meta import META_OP_REJECTED, MetaOpOut, parse_date, validate_op
from app.chat.nodes.analyze import AnalyzeQueryOut, ScopeDoc, apply_decision

D1 = ScopeDoc(uuid.uuid4(), "Services Agreement")
KEYS = {"D1": D1.id, str(D1.id): D1.id}


def op(**over: Any) -> MetaOpOut:
    base: dict[str, Any] = {
        "op": "COUNT_DOCUMENTS", "status": None, "category": None, "tag": None, "term": None,
        "field": None, "before": None, "after": None, "document_ref": None, "limit": None,
    }  # fmt: skip
    return MetaOpOut(**{**base, **over})


def decide(meta_op: MetaOpOut | None) -> Any:
    out = AnalyzeQueryOut(
        standalone_query="q", target_refs=[], dimensions=[], intent="meta", needs_whole_document=False,
        clarification=None, meta_op=meta_op,
    )  # fmt: skip
    return apply_decision(out, "q", [D1], clarification_allowed=True)


def test_unknown_op_rejected() -> None:
    assert validate_op(op(op="DROP_TABLE_DOCUMENTS"), KEYS) is None
    res = decide(op(op="DELETE_EVERYTHING"))
    assert res.intent == "qa" and res.meta_op is None and META_OP_REJECTED in res.reason_codes
    res = decide(None)  # meta without an op
    assert res.intent == "qa" and META_OP_REJECTED in res.reason_codes


def test_valid_op_routes_to_meta() -> None:
    res = decide(op(op="DOCUMENTS_MENTIONING", term="  Delaware "))
    assert res.intent == "meta" and res.meta_op is not None and res.meta_op.term == "Delaware"
    assert res.reason_codes == ()


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "DOCUMENTS_MENTIONING"},  # term required
        {"op": "DOCUMENT_INFO"},  # document_ref required
        {"op": "DOCUMENT_INFO", "document_ref": "D9"},  # not in scope
        {"op": "DOCUMENT_INFO", "document_ref": str(uuid.uuid4())},  # a foreign id
        {"op": "COUNT_DOCUMENTS", "status": "bogus"},
        {"op": "LIST_KEY_DATES", "field": "birthday"},
        {"op": "LIST_KEY_DATES", "before": "next year"},
    ],
)
def test_bad_parameters_rejected(bad: dict[str, Any]) -> None:
    assert validate_op(op(**bad), KEYS) is None


def test_parameters_normalized() -> None:
    m = validate_op(op(op="LIST_DOCUMENTS", category=" Contract ", limit=500), KEYS)
    assert m is not None and m.category == "contract" and m.limit == 20
    m = validate_op(op(op="DOCUMENT_INFO", document_ref="D1"), KEYS)
    assert m is not None and m.document_id == D1.id
    m = validate_op(op(op="LIST_KEY_DATES", field="Expiry_Date", before="2025-01-01"), KEYS)
    assert m is not None and m.field == "expiry_date" and m.before == dt.date(2025, 1, 1)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("June 1, 2020", dt.date(2020, 6, 1)),
        ("effective 1 June 2020 onward", dt.date(2020, 6, 1)),
        ("Dec. 31, 2024", dt.date(2024, 12, 31)),
        ("2020-06-01", dt.date(2020, 6, 1)),
        ("6/1/2020", dt.date(2020, 6, 1)),
        ("twelve months", None),
        ("February 30, 2020", None),
    ],
)
def test_parse_date(raw: str, want: dt.date | None) -> None:
    assert parse_date(raw) == want
