"""S12.07 pieces without a model: document selection, completeness, sampling, the spend cap, the CLI."""

import uuid
from decimal import Decimal

import pytest
from eval import brief_eval as be
from eval.judge import SpendCapReached, SpendGuard
from eval.loader import load_dataset

from app.insights.brief import Brief, StoredEvidence, StoredFact
from app.llm.gateway import MemoryLedger
from tests.support.brief_kit import brief_reply, chunk


def ev(text: str) -> StoredEvidence:
    return StoredEvidence(
        chunk_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_no=1,
        pages=[1],
        section_path=[],
        sentence_start=0,
        sentence_end=1,
        char_start=0,
        char_end=len(text),
        cited_text=text,
    )


def brief(*facts: tuple[str, str, str]) -> Brief:
    return Brief(
        document_type="x",
        category="contract",
        tags=[],
        summary="s",
        suggested_questions=[],
        facts=[
            StoredFact(field=f, value=v, status="verified", reason=None, evidence=[ev(t)])
            for f, v, t in facts
        ],
        reasons=[],
        partial=False,
    )


def test_train_documents_exclude_holdout() -> None:
    ds = load_dataset()
    keys = be.train_documents(ds, 99)
    assert len(keys) == 10 and keys == sorted(keys)
    assert not set(keys) & ds.holdout_docs
    assert be.train_documents(ds, 3) == keys[:3]


def test_completeness_targets_come_from_answerable_cuad_train_items() -> None:
    ds = load_dataset()
    ts = be.completeness_targets(ds, "cuad_consulting")
    assert {t.clause for t in ts} <= set(be.FIELD_MAP) and ts
    assert {t.clause for t in ts} >= {"Parties", "Governing Law"} - {"Parties"}
    assert be.completeness_targets(ds, "fb_amd_10k") == []  # not a CUAD document
    assert all(t.quote for t in ts)


def test_target_present_needs_the_mapped_field() -> None:
    t = be.Target("i", "Governing Law", ("governing_law",), "the laws of the State of Delaware")
    cited = "This Agreement is governed by the laws of the State of Delaware."
    assert be.target_present(t, brief(("governing_law", "Delaware", cited)))
    assert not be.target_present(t, brief(("key_point", "Delaware", cited)))  # wrong field
    assert not be.target_present(t, brief(("governing_law", "Delaware", "Payment is due in 30 days.")))
    assert not be.target_present(t, brief())


def test_sample_is_stable_and_bounded() -> None:
    b = brief(*[("key_point", f"fact {i}", "t") for i in range(30)])
    a = be.sample_facts("d", b, 10)
    assert a == be.sample_facts("d", b, 10) and len(a) == 10 and a == sorted(a)
    assert be.sample_facts("d", b, 0) == list(range(30)) and be.sample_facts("d", b, 40) == list(range(30))


def test_fact_case_is_blind_and_cited() -> None:
    c = be.fact_case("d", 3, "party", "Acme LLC", ["Acme LLC signs."])
    assert c.evidence == ("Acme LLC signs.",) and c.answer == "Party: Acme LLC"


async def test_guard_checks_before_every_call() -> None:
    gw = be._dry_gateway(MemoryLedger())
    prov = gw.chat  # type: ignore[attr-defined]
    guard = SpendGuard(MemoryLedger(), Decimal("0.0001"))  # below the worst case of one brief call
    g = be.GuardedGateway(gw, guard)
    from app.chat.evidence import build_evidence
    from app.insights.brief import brief_schema

    evidence = build_evidence([chunk("The fee is $5.")])
    with pytest.raises(SpendCapReached):
        await g.call(
            purpose="brief_extract",
            lane="background",
            prompt=("brief_extract", 2),
            owner_id=uuid.uuid4(),
            schema=brief_schema(evidence),
            messages=[{"role": "user", "content": evidence.text}],
        )
    assert prov.requests == []  # nothing was sent


def test_live_run_refuses_without_cap(capsys: pytest.CaptureFixture[str]) -> None:
    assert be.main(["--docs", "1"]) == 2
    assert "--max-usd" in capsys.readouterr().err


def test_freeze_needs_live() -> None:
    with pytest.raises(SystemExit):
        be.main(["--docs", "1", "--dry-run", "--freeze"])


def test_reply_fixture_is_valid() -> None:  # keeps brief_reply imported for the integration twin
    assert brief_reply().parts
