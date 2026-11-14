"""Judge output validates against its schema for sampled dataset items, per judge type.
FakeProvider scripted replies; no live calls. Also checks the strict request schema and routed roles."""

import pytest
from eval import judge as J
from eval.loader import load_dataset

from tests.support.chat_gen import fake_gateway
from tests.support.judgekit import reply


@pytest.mark.parametrize("jt", J.JUDGE_TYPES)
async def test_schema_valid(jt: J.JudgeType) -> None:
    items = load_dataset().select("train")
    cases = J.sample_cases(items, jt, 20)
    assert len(cases) >= (
        20 if jt != "rubric" else 3
    )  # rubric: only 3 train items carry a rubric (x2 variants)
    verdicts = ["pass", "partial", "fail"]
    script = [reply(jt, verdicts[i % 3], n_criteria=len(c.criteria) or 1) for i, c in enumerate(cases)]
    gw, fake = fake_gateway(script)
    for i, c in enumerate(cases):
        v = await J.run_judge(gw, c, jt)
        assert v.verdict == verdicts[i % 3] and 0.0 <= v.confidence <= 1.0 and v.reason
        assert v.role == "deep"
    for req in fake.requests:
        s = req.output_schema
        assert (
            s is not None and set(s["required"]) == set(s["properties"]) and "confidence" in s["properties"]
        )
        assert req.system.startswith("You are a strict grader")
    assert len(fake.requests) == len(cases)
    # secondary tier is routed to flagship by role, with the same strict schema
    gw2, fake2 = fake_gateway([reply(jt, n_criteria=len(cases[0].criteria) or 1)])
    sv = await J.run_judge(gw2, cases[0], jt, secondary=True)
    assert sv.role == "flagship" and sv.tier == "secondary"
    assert fake2.requests[0].model == gw2.router.role_models["flagship"]
