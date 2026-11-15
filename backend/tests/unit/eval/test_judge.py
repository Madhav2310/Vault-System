"""Judge bias controls (tiers, blindness), the judges' view of an answer, the spend cap and the CLI."""

from decimal import Decimal

import pytest
from eval import judge as J

from app.core.settings import Settings
from app.llm.routing import ModelRouter, Route
from tests.support.evalkit import chunk, item, out
from tests.support.judgekit import request_text
from tests.unit.eval.test_claim_judge import gateway, literal_claim_judge

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
CHUNKS = {"c1": C1}
GEN_MODEL = "gpt-6.1-terra"
FEE = item(
    required_claims=["The fee is $1,000.", "The fee is due within 30 days."],
    gold_evidence=[{"doc": "d1", "sentence_start": 0, "sentence_end": 1}],
)


def test_tier_rule() -> None:
    # default routing satisfies judge tier >= generator tier (balanced for answers, deep for compare)
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    assert J.check_tiers(ModelRouter.from_settings(s)) == ("deep", "flagship")
    low = ModelRouter.from_settings(s)
    low.routes["eval_judge"] = Route("balanced", "low")
    low.routes["generate:compare"] = Route("deep", "low")
    with pytest.raises(J.TierError):
        J.check_tiers(low)


async def test_judge_input_is_blind() -> None:
    gw, fake = gateway(literal_claim_judge)
    await J.judge_claims(gw, FEE, f"As {GEN_MODEL} I say the fee is $1,000 (gpt-6-astra), due in 30 days.")
    text = request_text(fake.requests[0])
    for model in gw.router.role_models.values():
        assert model not in text
    assert "[model]" in text
    with pytest.raises(J.BlindnessError):
        J.assert_blind({"answer": f"by {GEN_MODEL}"}, [GEN_MODEL])


def test_segment_views_carry_only_stored_passages() -> None:
    views = J.segment_views(out(("Virginia.", [("c1", 0, 1)]), ("No cite.", [])), CHUNKS)
    assert [v.text for v in views] == ["Virginia.", "No cite."]
    assert views[0].passages == ("The Agreement is governed by the laws of Virginia.",)
    assert views[1].passages == ()
    # an unknown chunk and an out-of-range citation contribute no passage
    assert J.segment_views(out(("x", [("zz", 0, 1), ("c1", 5, 9)])), CHUNKS)[0].passages == ()


async def test_spend_cap_checked_before_each_call() -> None:
    gw, fake = gateway(literal_claim_judge)
    tiny = J.SpendGuard(gw.ledger, Decimal("0.0000001"))  # type: ignore[arg-type]
    with pytest.raises(J.SpendCapReached):
        await J.judge_claims(gw, FEE, "The fee is $1,000.", guard=tiny)
    assert fake.requests == []  # refused before any call
    ok = J.SpendGuard(gw.ledger, Decimal("1"))  # type: ignore[arg-type]
    await J.judge_claims(gw, FEE, "The fee is $1,000.", guard=ok)
    records = gw.ledger.records  # type: ignore[attr-defined]
    assert len(records) == 1 and ok.spent == records[0].cost_usd > 0


def test_cli_needs_a_task_and_a_cap(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        J.main([])
    assert "nothing to do" in capsys.readouterr().err
    assert J.main(["--calibrate", "labels.yaml"]) == 2
    assert "--max-usd" in capsys.readouterr().err
