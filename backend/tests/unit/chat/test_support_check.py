"""Support check: repair of model verdicts, what the model sees, failure policy. FakeProvider only."""

import json
import uuid
from typing import Any

from app.chat.citations import EvidenceReference, ResolvedSegment
from app.chat.support_check import (
    MAX_REASON_CHARS,
    SUPPORT_CHECK_FAILED,
    SupportOut,
    VerdictOut,
    apply_verdicts,
    build_inputs,
    check_support,
)
from app.llm import errors
from app.llm.providers.fake import refusal, structured, timeout
from tests.support.chat_gen import fake_gateway

OWNER = uuid.uuid4()


def ref(text: str) -> EvidenceReference:
    return EvidenceReference(uuid.uuid4(), uuid.uuid4(), 0, 1, 0, len(text), text)


SEGS = (
    ResolvedSegment(0, "Twelve months.", "cited", (ref("The Services run for twelve months."),)),
    ResolvedSegment(1, "UNCITED-SECRET claim.", "none", ()),
    ResolvedSegment(2, "Delaware law applies.", "cited", (ref("Governed by Delaware law."),)),
)


def verdicts(*items: tuple[int, str, str]) -> Any:
    return structured({"verdicts": [{"index": i, "verdict": v, "reason": r} for i, v, r in items]})


def test_repair() -> None:
    out = SupportOut(
        verdicts=[
            VerdictOut(index=0, verdict="supported", reason="matches"),
            VerdictOut(index=0, verdict="unsupported", reason="duplicate is ignored"),
            VerdictOut(index=1, verdict="supported", reason="uncited segment was not asked"),
            VerdictOut(index=99, verdict="partial", reason="unknown index"),
        ]
    )
    got = apply_verdicts(SEGS, out, asked={0, 2})
    assert set(got) == {0, 1, 2}  # unknown index 99 dropped
    assert (got[0].verdict, got[0].reason) == ("supported", "matches")  # first verdict wins
    assert got[1].verdict == "unchecked"  # support=none stays unchecked even if the model returned a verdict
    assert got[2].verdict == "unchecked"  # no verdict -> unchecked


def test_reason_is_capped() -> None:
    out = SupportOut(verdicts=[VerdictOut(index=0, verdict="partial", reason="x " * 200)])
    got = apply_verdicts(SEGS, out, asked={0})
    assert got[0].reason is not None and len(got[0].reason) <= MAX_REASON_CHARS


def test_model_sees_only_cited_text() -> None:
    inputs = build_inputs(SEGS)
    blob = json.dumps(inputs)
    assert [s["index"] for s in inputs["segments"]] == [0, 2]
    assert "UNCITED-SECRET" not in blob
    assert inputs["segments"][0]["cited"] == ["The Services run for twelve months."]
    assert "Governed by Delaware law." not in json.dumps(inputs["segments"][0])  # other segment's citation


async def test_no_cited_segment_no_call() -> None:
    gw, prov = fake_gateway([])
    res = await check_support(gw, (SEGS[1],), owner_id=OWNER)
    assert not res.called and res.reasons == () and res.verdicts[1].verdict == "unchecked"
    assert prov.requests == []


async def test_verdicts_returned() -> None:
    gw, prov = fake_gateway([verdicts((0, "supported", "same fact"), (2, "unsupported", "no Delaware"))])
    res = await check_support(gw, SEGS, owner_id=OWNER)
    assert len(prov.requests) == 1 and "UNCITED-SECRET" not in str(prov.requests[0])
    assert res.called and res.reasons == ()
    assert [res.verdicts[i].verdict for i in (0, 1, 2)] == ["supported", "unchecked", "unsupported"]


async def test_failures_degrade_to_unchecked() -> None:
    for step in (refusal(), timeout(), structured({"nope": 1})):
        gw, _ = fake_gateway([step])
        res = await check_support(gw, SEGS, owner_id=OWNER)
        assert res.reasons == (SUPPORT_CHECK_FAILED,)
        assert {v.verdict for v in res.verdicts.values()} == {"unchecked"}


async def test_budget_exhausted_degrades() -> None:
    class Spent:
        deadline_at = None

        def charge_call(self) -> None:
            raise errors.BudgetExceededError("turn call budget exhausted")

        def record_usage(self, usage: Any) -> None: ...

    gw, _ = fake_gateway([verdicts((0, "supported", "ok"))])
    res = await check_support(gw, SEGS, owner_id=OWNER, budget=Spent())
    assert res.reasons == (SUPPORT_CHECK_FAILED,)
