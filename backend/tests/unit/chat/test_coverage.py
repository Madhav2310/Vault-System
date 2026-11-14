"""S22.04 plan check (`app/chat/coverage.py`): pure code, no model."""

from typing import Any

from app.chat.citations import Resolution, ResolvedSegment
from app.chat.coverage import (
    ELEMENT_NOT_FOUND,
    MAX_ELEMENTS,
    apply_plan,
    check_plan,
    element_ids,
    required_elements,
)
from tests.support.chat_gen import evidence

EVID = evidence()  # handles E1.0, E1.1, E2.0
ELS = ("notice period", "governing law")


def _item(i: int, handles: list[str], nf: bool = False) -> dict[str, Any]:
    return {"element": f"R{i}", "handles": handles, "not_found": nf}


def test_all_valid() -> None:
    c = check_plan([_item(1, ["E1.1"]), _item(2, ["E2.0"])], ELS, EVID)
    assert c is not None and c.not_found == () and c.covered == ELS


def test_partial_and_none() -> None:
    c = check_plan([_item(1, ["E1.1"]), _item(2, [], True)], ELS, EVID)
    assert c is not None and c.not_found == ("governing law",)
    c = check_plan([_item(1, ["E9.9"]), _item(2, [])], ELS, EVID)  # unknown handles do not count
    assert c is not None and c.not_found == ELS


def test_handles_decide_not_the_flag() -> None:
    c = check_plan([_item(1, ["E1.1"], True), _item(2, [], False)], ELS, EVID)
    assert c is not None and c.not_found == ("governing law",)


def test_duplicates_merge_and_omitted_element_is_not_found() -> None:
    c = check_plan([_item(1, []), _item(1, ["E1.0"])], ELS, EVID)
    assert c is not None and c.covered == ("notice period",) and c.not_found == ("governing law",)


def test_max_four_elements() -> None:
    assert len(required_elements(["a", "b", "c", "d", "e", "f"])) == MAX_ELEMENTS
    assert required_elements(["a", " a ", "", "b"]) == ("a", "b")
    assert element_ids(3) == ("R1", "R2", "R3")


def test_malformed_plan_degrades() -> None:
    for bad in (None, "x", 5, {}, [], [1, "a"], [{"element": 3}], [{"element": "R7", "handles": "E1.0"}]):
        c = check_plan(bad, ELS, EVID)  # never raises
        assert c is None or c.covered == ()  # an unusable plan never invents coverage
    assert check_plan(None, ELS, EVID) is None and check_plan([], ELS, EVID) is None


def test_apply_appends_template_segment_and_reason() -> None:
    seg = ResolvedSegment(0, "Thirty days.", "cited", ())
    c = check_plan([_item(1, ["E1.1"]), _item(2, [])], ELS, EVID)
    assert c is not None
    res, reasons = apply_plan(Resolution((seg,)), c)
    assert [s.ordinal for s in res.segments] == [0, 1]
    assert res.segments[1].support == "none" and "governing law" in res.segments[1].text
    assert reasons == (ELEMENT_NOT_FOUND,)
    ok = check_plan([_item(1, ["E1.1"]), _item(2, ["E2.0"])], ELS, EVID)
    assert ok is not None
    same, none = apply_plan(Resolution((seg,)), ok)
    assert same.segments == (seg,) and none == ()
