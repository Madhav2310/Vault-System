"""Named-document retrieval (CHAT_NAMED_DOCS): detection from the question text, one retrieval per named
document, and the deterministic merge into the global selection (no database, no model)."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.chat.named_docs import (
    apply_named,
    detect_named,
    merge_named,
    named_on,
    retrieve_named,
)
from app.chat.nodes.analyze import ScopeDoc
from app.core.settings import Settings
from app.retrieval.retrieve import Candidate, RetrievalResult
from app.retrieval.scope import Scope


def _doc(n: int, title: str, lead: str = "") -> ScopeDoc:
    return ScopeDoc(uuid.UUID(int=n), title, lead)


INSURANCE = _doc(
    1,
    "insurance_summary",
    "Commercial Vehicle Insurance: Summary of Cover\n\nPolicy HCS-88021 insures the fleet of Hollis Courier "
    "Services Ltd with Brackley Mutual Insurance, from 1 January",
)
SLA = _doc(
    2,
    "sla_fibrenet",
    "Business Internet Service Level Agreement\n\nFibrenet Connectivity Ltd provides dedicated internet "
    "access to Harcourt Print Works under Service Order 7714",
)
WARRANTY = _doc(
    3, "warranty_terms", "Norden Appliances: Limited Warranty\n\nThis warranty covers Norden household"
)
HANDBOOK = _doc(
    4, "handbook_leave", "Orchard Analytics Employee Handbook: Leave and Time Off\n\nThis chapter applies"
)
EXPENSE = _doc(
    5,
    "policy_expense_v2",
    "Employee Travel and Expense Policy (Revised)\n\nPolicy owner: Finance Operations, Alder & Finch Design",
)
VANTAGE = _doc(6, "consulting_vantage", "Consulting Agreement\n\nVantage Partners engages")
VANTAGE2 = _doc(7, "consulting_vantage_amend1", "First Amendment\n\nVantage Partners amends")
CUAD_GAMING = _doc(8, "ENTERTAINMENTGAMINGASIAINC_02_15_2005-EX-10.5-DISTRIBUTOR AGREEMENT")
CUAD_LICENSE = _doc(9, "GpaqAcquisitionHoldingsInc_20200123_S-4A_EX-10.6_11951677_EX-10.6_License Agreement")
PAYPAL = _doc(10, "PayPal_2023Q2_10Q", "Commission file number 001-36859")
LEASE = _doc(
    11, "lease_harbor_v2", "Office Lease Agreement\n\nThis lease is made between Harbor Street Estates"
)
ALL = [
    INSURANCE,
    SLA,
    WARRANTY,
    HANDBOOK,
    EXPENSE,
    VANTAGE,
    VANTAGE2,
    CUAD_GAMING,
    CUAD_LICENSE,
    PAYPAL,
    LEASE,
]


Q_THREE = (
    "How quickly must a claim be raised under the Hollis motor policy, "
    "the Fibrenet credits and the Norden warranty?"
)


def test_switch_defaults_off() -> None:
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    assert s.chat_named_docs is False and s.chat_named_docs_k == 3
    assert not named_on(None) and named_on(SimpleNamespace(chat_named_docs=True))


def test_names_in_the_opening_text_find_documents_whose_title_is_a_file_stem() -> None:
    q = Q_THREE
    assert detect_named(q, ALL) == [INSURANCE.id, SLA.id, WARRANTY.id]


def test_documents_come_in_the_order_the_question_names_them() -> None:
    q = "Is the Norden warranty longer than the Fibrenet repair window?"
    assert detect_named(q, ALL) == [WARRANTY.id, SLA.id]


def test_opening_text_names_need_a_capitalised_mention_in_the_question() -> None:
    assert detect_named("do hollis and fibrenet differ on claims", ALL) == [
        SLA.id
    ]  # only the title word names
    assert detect_named("Compare Hollis with Fibrenet", ALL) == [INSURANCE.id, SLA.id]


def test_title_words_name_a_document_in_any_case() -> None:
    assert detect_named("which is longer, the internet sla or the warranty?", ALL) == [SLA.id, WARRANTY.id]
    named = detect_named("what does the lease say about notice for the paypal filing", ALL)
    assert set(named) == {LEASE.id, PAYPAL.id}


def test_glued_upper_case_names_match_a_prefix_or_the_words_joined() -> None:
    q = "Compare the GPAQ license agreement with the Entertainment Gaming Asia distributor agreement."
    assert set(detect_named(q, ALL)) == {CUAD_LICENSE.id, CUAD_GAMING.id}


def test_a_word_shared_by_documents_names_none_of_them() -> None:
    assert detect_named("What hourly rate does Vantage charge, and for how long?", ALL) == []
    assert detect_named("What hourly rate does Vantage charge, and for how long?", ALL, max_df=2) != []


def test_common_words_in_the_question_name_nothing() -> None:
    assert detect_named("How long does the contract extend beyond its initial term?", ALL) == []
    assert detect_named("What is the notice period and the payment schedule?", ALL) == []


def test_sentence_initial_capital_is_not_a_name() -> None:
    assert detect_named("Orchard? And Alder & Finch?", ALL) != []
    assert detect_named("Employee notice. Hollis only.", ALL) == []


def test_at_most_five_documents_are_named() -> None:
    docs = [_doc(100 + i, f"zeta{i}name_doc") for i in range(7)]
    q = " ".join(f"zeta{i}name" for i in range(7))
    assert len(detect_named(q, docs)) == 5


def _cand(
    version: uuid.UUID, rrf: float, *, selected: bool = False, rerank: float | None = None
) -> Candidate:
    return Candidate(uuid.uuid4(), version, rrf_score=rrf, rerank_score=rerank, selected=selected)


def _result(cands: list[Candidate], selected: list[Candidate] | None = None) -> RetrievalResult:
    sel = [c for c in cands if c.selected] if selected is None else selected
    return RetrievalResult(tuple(cands), tuple(c.chunk_id for c in sel), (), "retrieval@2")


V = [uuid.uuid4() for _ in range(3)]


def test_a_named_document_missing_from_the_global_selection_gets_its_best_chunks() -> None:
    g = [_cand(V[0], 0.03 - i / 1000, selected=True) for i in range(4)]
    own = [_cand(V[1], 0.02 - i / 1000, selected=True) for i in range(3)]
    out = merge_named(_result(g), [(V[1], _result(own))], k=3)
    assert out.selected == (*(c.chunk_id for c in g), *(c.chunk_id for c in own))
    rows = {c.chunk_id: c for c in out.candidates}
    assert all(rows[c.chunk_id].selected for c in own)
    assert [rows[c.chunk_id].result_index for c in own] == [4, 5, 6]
    assert rows[own[0].chunk_id].rrf_score == own[0].rrf_score  # per-document scores are kept as provenance


def test_a_document_already_holding_k_global_chunks_adds_nothing() -> None:
    g = [_cand(V[0], 0.03 - i / 1000, selected=True) for i in range(3)]
    out = merge_named(_result(g), [(V[0], _result([_cand(V[0], 0.5, selected=True)]))], k=3)
    assert out is not None and out.selected == tuple(c.chunk_id for c in g)


def test_a_document_is_topped_up_to_k_not_by_k() -> None:
    g = [_cand(V[0], 0.03, selected=True), _cand(V[1], 0.02, selected=True)]
    own = [_cand(V[1], 0.9 - i / 10, selected=True) for i in range(3)]
    out = merge_named(_result(g), [(V[1], _result(own))], k=3)
    assert len(out.selected) == 4  # one global chunk of V[1] plus its two best chunks it lacked
    assert out.selected[2:] == (own[0].chunk_id, own[1].chunk_id)


def test_a_chunk_the_global_run_scored_keeps_its_global_row_and_is_not_duplicated() -> None:
    shared = _cand(V[1], 0.01)  # scored globally but not selected
    g = [_cand(V[0], 0.03, selected=True), shared]
    own = dataclasses_replace(shared, rrf_score=0.5, selected=True)
    out = merge_named(_result(g), [(V[1], _result([own]))], k=3)
    rows = [c for c in out.candidates if c.chunk_id == shared.chunk_id]
    assert len(rows) == 1 and rows[0].selected and rows[0].rrf_score == 0.01
    assert shared.chunk_id in out.selected


def dataclasses_replace(c: Candidate, **kw: Any) -> Candidate:
    import dataclasses

    return dataclasses.replace(c, **kw)


def test_merge_is_deterministic_and_keeps_the_naming_order() -> None:
    g = [_cand(V[0], 0.03, selected=True)]
    a = [_cand(V[1], 0.2, selected=True)]
    b = [_cand(V[2], 0.1, selected=True)]
    first = merge_named(_result(g), [(V[2], _result(b)), (V[1], _result(a))], k=1)
    assert first.selected == (g[0].chunk_id, b[0].chunk_id, a[0].chunk_id)
    assert merge_named(_result(g), [(V[2], _result(b)), (V[1], _result(a))], k=1) == first


def test_degradation_of_a_per_document_retrieval_is_carried() -> None:
    g = [_cand(V[0], 0.03, selected=True)]
    own = RetrievalResult((_cand(V[1], 0.1, selected=True),), (), ("no_rerank",), "retrieval@2")
    own = RetrievalResult(
        own.candidates, tuple(c.chunk_id for c in own.candidates), own.degraded, own.config_version
    )
    assert merge_named(_result(g), [(V[1], own)], k=3).degraded == ("no_rerank",)


class FakeRetriever:
    """Retrieves two chunks of the single document in the scope it is given; one document fails when asked."""

    def __init__(self, fail_for: uuid.UUID | None = None) -> None:
        self.calls: list[Scope] = []
        self.embeds = 0
        self.fail_for = fail_for
        self.active = 0
        self.peak = 0

    async def embed(self, query: str, owner_id: uuid.UUID) -> str:
        self.embeds += 1
        return "emb"

    async def retrieve(
        self, sm: Any, owner_id: uuid.UUID, scope: Scope, query: str, *, embedding: Any = None
    ) -> RetrievalResult:
        self.calls.append(scope)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        ((_, version),) = scope.entries
        if version == self.fail_for:
            raise RuntimeError("boom")
        assert embedding == "emb"
        cands = [_cand(version, 0.5 - i / 10, selected=True) for i in range(2)]
        return _result(cands)


def _scope(docs: list[ScopeDoc]) -> Scope:
    return Scope(entries=tuple((d.id, uuid.UUID(int=d.id.int + 1000)) for d in docs))


@pytest.mark.asyncio
async def test_each_named_document_is_retrieved_alone_with_one_shared_embedding() -> None:
    scope = _scope(ALL)
    fake = FakeRetriever()
    out = await retrieve_named(fake, None, uuid.uuid4(), scope, [SLA.id, WARRANTY.id], "q")  # type: ignore[arg-type]
    assert [len(c.entries) for c in fake.calls] == [1, 1]
    assert {c.entries[0][0] for c in fake.calls} == {SLA.id, WARRANTY.id}
    assert fake.embeds == 1 and [v for v, _ in out] == [
        uuid.UUID(int=SLA.id.int + 1000),
        uuid.UUID(int=WARRANTY.id.int + 1000),
    ]
    assert fake.peak >= 2  # concurrent


@pytest.mark.asyncio
async def test_a_failed_per_document_retrieval_is_dropped_not_raised() -> None:
    scope = _scope(ALL)
    fake = FakeRetriever(fail_for=uuid.UUID(int=SLA.id.int + 1000))
    out = await retrieve_named(fake, None, uuid.uuid4(), scope, [SLA.id, WARRANTY.id], "q")  # type: ignore[arg-type]
    assert [v for v, _ in out] == [uuid.UUID(int=WARRANTY.id.int + 1000)]


@pytest.mark.asyncio
async def test_apply_named_needs_two_named_documents_in_the_scope() -> None:
    scope = _scope(ALL)
    base = _result([_cand(scope.entries[0][1], 0.1, selected=True)])
    fake = FakeRetriever()
    out, info = await apply_named(
        fake, None, uuid.uuid4(), ALL, scope, "Tell me about the Norden warranty", base
    )  # type: ignore[arg-type]
    assert out is base and info == {"named_docs": 1} and fake.calls == []
    narrowed = _scope([SLA, HANDBOOK])
    out, info = await apply_named(
        fake, None, uuid.uuid4(), ALL, narrowed, "Hollis versus Fibrenet versus Norden", base
    )  # type: ignore[arg-type]
    assert info["named_docs"] == 1 and out is base  # only Fibrenet is still in scope


@pytest.mark.asyncio
async def test_apply_named_merges_per_document_chunks_and_reports_counts() -> None:
    scope = _scope(ALL)
    glob = [_cand(scope.entries[0][1], 0.3 - i / 100, selected=True) for i in range(5)]
    q = Q_THREE
    out, info = await apply_named(FakeRetriever(), None, uuid.uuid4(), ALL, scope, q, _result(glob), k=2)  # type: ignore[arg-type]
    assert info == {"named_docs": 3, "named_added": 4}  # the Hollis document already holds 5 global chunks
    assert len(out.selected) == 9
    assert len({c.chunk_id for c in out.candidates}) == len(out.candidates)
