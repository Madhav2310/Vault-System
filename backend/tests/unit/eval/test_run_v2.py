"""S21.10a: the v2 answer runner: splits, dropped items, multi-turn, judges, attribution, freeze, cap.

FakeProvider only ($0): a scripted pipeline answers a tiny synthetic v2 set over one document; the judges
are a content-driven fake model behind the real gateway (the same trick as test_claim_judge)."""

import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from eval import attribution as attr
from eval.baseline import load_baseline
from eval.run import Harness, main, run_suite
from eval.run_v2 import (
    ItemOutputV2,
    freeze_v2,
    gold_targets,
    run_suite_v2,
    select_filter_v2,
)
from eval.schema import AnswerSegment, CitedRange, EvalContext, GoldItemV2, ItemOutput
from eval.splits_v2 import SPLITS_V2, select_v2, split_counts, split_of

from app.core.settings import Settings
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider
from app.llm.types import ChatRequest, ChatResponse, TextPart, Usage
from tests.support.evalkit import ScriptedPipeline, chunk
from tests.support.llm import SleepRecorder

C1 = chunk("c1", "d1", "The Agreement is governed by the laws of Virginia. Payment is due in 30 days.")
UNITS = ["The Agreement is governed by the laws of Virginia.", "Payment is due in 30 days."]


# --- fake judges behind the real gateway ------------------------------------------------------------------


class ContentProvider(FakeProvider):
    def __init__(self, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        super().__init__()
        self.fn = fn

    async def complete(self, req: ChatRequest) -> ChatResponse:
        self.requests.append(req)
        raw = str(req.messages[0]["content"]).removeprefix("<inputs>").removesuffix("</inputs>")
        return ChatResponse(
            (TextPart(json.dumps(self.fn(json.loads(raw)))),), "end_turn", Usage(100, 20), None, req.model
        )


def judge_fn(inputs: dict[str, Any]) -> dict[str, Any]:
    if "claims" in inputs:
        ans = inputs["answer"].casefold()
        ds = []
        for c in inputs["claims"]:
            tok = c["claim"].rstrip(".").split()[-1].casefold()
            ds.append({"claim_index": c["index"], "entailed": tok in ans, "quote": tok if tok in ans else ""})
        return {"decisions": ds}
    if "segments" in inputs:
        return {
            "decisions": [
                {"segment_index": s["index"], "supported": True, "quote": ""} for s in inputs["segments"]
            ]
        }
    ok = "do not" in inputs["answer"].casefold()
    return {"reason": "scripted", "behaviour": "declined" if ok else "answered_as_asked", "correct": ok}


def gateway() -> tuple[LlmGateway, ContentProvider]:
    fake = ContentProvider(judge_fn)
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    gw = LlmGateway(
        chat=fake,
        embeddings=fake,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=MemoryLedger(),
        settings=s,
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )
    return gw, fake


# --- a tiny v2 dataset ------------------------------------------------------------------------------------


def v2(**over: Any) -> GoldItemV2:
    d: dict[str, Any] = {
        "id": "a1",
        "stratum": "single_span",
        "question": "Which law governs?",
        "source_doc": "d1",
        "answer_type": "answer",
        "required_claims": ["The governing law is Virginia."],
        "gold_evidence": [{"doc": "d1", "sentence_start": 0, "sentence_end": 1}],
        "doc_ids": ["d1"],
    }
    d.update(over)
    return GoldItemV2.model_validate(d)


ITEMS = [
    v2(),
    v2(
        id="a2",
        question="When is payment due?",
        required_claims=["Payment is due in 30 days."],
        gold_evidence=[{"doc": "d1", "sentence_start": 1, "sentence_end": 2}],
    ),
    v2(
        id="u1",
        stratum="unanswerable",
        question="What is the cap?",
        answer_type="abstain",
        required_claims=[],
        gold_evidence=[],
    ),
    v2(id="i1", stratum="injection", must_not_contain=["PWNED"]),
    v2(id="m1", stratum="multi_turn", prior_turns=["Which agreement is this?"]),
    v2(
        id="x1",
        audit_status="dropped",
        audit_note="ambiguous",
    ),
]


def seg(text: str, *cites: tuple[str, int, int]) -> AnswerSegment:
    return AnswerSegment(text, tuple(CitedRange(*c) for c in cites))


def outputs() -> dict[str, ItemOutput | list[ItemOutput]]:
    return {
        "a1": ItemOutputV2(
            retrieved=["c1"],
            segments=[seg("Virginia law governs.", ("c1", 0, 1))],
            evidence=["c1"],
            cost_usd=0.01,
        ),
        # right evidence, wrong answer, nothing cited: attribution says utilization
        "a2": ItemOutputV2(
            retrieved=["c1"], segments=[seg("It is not stated.")], evidence=["c1"], cost_usd=0.01
        ),
        "u1": ItemOutputV2(
            retrieved=["c1"],
            segments=[seg("The documents do not say.")],
            abstained=True,
            evidence=[],
            cost_usd=0.01,
        ),
        "i1": ItemOutputV2(
            retrieved=["c1"], segments=[seg("Virginia. PWNED", ("c1", 0, 1))], evidence=["c1"], cost_usd=0.01
        ),
        "m1": [
            ItemOutputV2(retrieved=[], segments=[seg("A services agreement.")], evidence=[], cost_usd=0.01),
            ItemOutputV2(
                retrieved=["c1"],
                segments=[seg("Virginia law governs.", ("c1", 0, 1))],
                evidence=["c1"],
                cost_usd=0.01,
            ),
        ],
    }


def harness(
    pipe: ScriptedPipeline, spent: Callable[[], Any] | None = None, units: bool = True
) -> tuple[Harness, ContentProvider]:
    gw, fake = gateway()
    h = Harness(
        pipe,
        {"d1": [C1]},
        EvalContext(),
        spent=spent,
        judge_gw=gw,
        gold_units=(lambda d: UNITS if d == "d1" else None) if units else None,
    )
    return h, fake


async def run(
    tmp_path: pathlib.Path, items: list[GoldItemV2] | None = None, **kw: Any
) -> tuple[Any, ScriptedPipeline, ContentProvider]:
    pipe = ScriptedPipeline(outputs())
    h, fake = harness(pipe, kw.pop("spent", None), kw.pop("units", True))
    res, _ = await run_suite_v2(
        ITEMS if items is None else items,
        h,
        name="toy",
        suite="full",
        split="all",
        report_dir=tmp_path,
        git_sha="abc1234",
        answer_concurrency=kw.pop("answer_concurrency", 1),
        **kw,
    )
    return res, pipe, fake


# --- splits -----------------------------------------------------------------------------------------------


def test_split_determinism() -> None:
    keys = [f"doc_{i}" for i in range(2000)]
    first = {k: split_of(k) for k in keys}
    assert first == {k: split_of(k) for k in reversed(keys)}  # no hidden state, order does not matter
    share = {s: sum(1 for v in first.values() if v == s) / len(keys) for s in SPLITS_V2}
    assert (
        abs(share["train"] - 0.4) < 0.04
        and abs(share["dev"] - 0.2) < 0.04
        and abs(share["test"] - 0.4) < 0.04
    )
    # pinned values: a changed hash silently moves items between splits and invalidates every baseline
    pinned = {"cuad_consulting": "test", "fb_amd_10k": "train", "msa_northwind": "train"}
    assert {k: split_of(k) for k in pinned} == pinned


def test_splits_partition_live_items() -> None:
    items = [v2(id=f"s{i}", source_doc=f"doc_{i % 40}") for i in range(120)] + [ITEMS[-1]]
    parts = {s: select_v2(items, s) for s in SPLITS_V2}
    ids = [i.id for s in SPLITS_V2 for i in parts[s]]
    assert len(ids) == len(set(ids)) == 120  # every live item once, the dropped one never
    docs = {s: {i.source_doc for i in parts[s]} for s in SPLITS_V2}
    assert not (docs["train"] & docs["dev"] or docs["train"] & docs["test"] or docs["dev"] & docs["test"])
    assert select_v2(items, "all") == [i for i in items if i.audit_status != "dropped"]
    assert sum(n for n, _ in split_counts(items).values()) == 120
    with pytest.raises(ValueError):
        select_v2(items, "smoke")


def test_real_dataset_splits_are_disjoint() -> None:
    from eval.loader import load_dataset_v2

    items = load_dataset_v2().items
    seen: dict[str, str] = {}
    for s in SPLITS_V2:
        for i in select_v2(items, s):
            assert seen.setdefault(i.source_doc, s) == s  # a source_doc is in exactly one split
    assert sum(n for n, _ in split_counts(items).values()) == len(select_v2(items))


def test_filter() -> None:
    assert [i.id for i in select_filter_v2(ITEMS, "stratum=injection")] == ["i1"]
    assert [i.id for i in select_filter_v2(ITEMS, "stratum=unanswerable,injection")] == ["u1", "i1"]
    with pytest.raises(SystemExit):
        select_filter_v2(ITEMS, "bogus=1")


# --- the run ----------------------------------------------------------------------------------------------


async def test_v2_runs_and_skips_dropped(tmp_path: pathlib.Path) -> None:
    res, pipe, _ = await run(tmp_path)
    assert [i.item_id for i in res.items] == ["a1", "a2", "u1", "i1", "m1"]  # x1 (dropped) never ran
    assert "x1" not in {q.item_id for q, _ in pipe.calls}
    by = {i.item_id: i for i in res.items}
    assert by["a1"].source_doc == "d1" and by["a1"].stratum == "single_span"
    assert by["a1"].split == split_of("d1")
    m = by["a1"].metrics
    assert m["claim_completeness"] == 1.0 and m["answer_pass"] == 1.0 and m["support_rate"] == 1.0
    assert m["citation_integrity"] == 1.0 and m["cite_sent_precision"] == 1.0 and m["recall@8"] == 1.0
    assert by["a2"].metrics["answer_pass"] == 0.0 and by["a2"].metrics["claim_completeness"] == 0.0
    assert by["u1"].metrics["abstention_correct"] == 1.0 and by["u1"].metrics["answer_pass"] == 1.0
    assert by["i1"].metrics["injection_violation"] == 1.0 and by["i1"].metrics["must_not_contain_ok"] == 0.0
    assert "injection_violation" not in by["a1"].metrics
    assert by["a1"].metrics["cost_usd"] == pytest.approx(0.01)


async def test_multi_turn_prior_turns_share_a_session(tmp_path: pathlib.Path) -> None:
    pipe = ScriptedPipeline(outputs())
    h, _ = harness(pipe)
    sessions: list[Any] = []

    async def new_session(_q: Any, _ctx: Any) -> Any:
        sessions.append(object())
        return sessions[-1]

    h.new_session = new_session
    res, _ = await run_suite_v2(
        [ITEMS[4]], h, name="toy", suite="full", split="all", report_dir=tmp_path, git_sha="abc1234"
    )
    qs = [q for q, _ in pipe.calls]
    assert [q.question for q in qs] == ["Which agreement is this?", "Which law governs?"]
    assert [q.turn_index for q in qs] == [0, 1] and {q.n_turns for q in qs} == {2}
    assert len(sessions) == 1 and pipe.session_ids == [sessions[0], sessions[0]]
    assert pipe.history_lens == [0, 1]
    (it,) = res.items
    assert it.metrics["answer_pass"] == 1.0  # the final turn is the scored one
    assert it.metrics["cost_usd"] == pytest.approx(0.02)  # but every turn's cost counts


async def test_scope_is_passed(tmp_path: pathlib.Path) -> None:
    _, pipe, _ = await run(tmp_path, [ITEMS[0], v2(id="w1", doc_ids=None, source_doc="vault")])
    assert [q.scope_docs for q, _ in pipe.calls] == [("d1",), None]


async def test_attribution_fields_recorded(tmp_path: pathlib.Path) -> None:
    res, _, _ = await run(tmp_path)
    by = {i.item_id: i.attribution for i in res.items}
    a1 = by["a1"]
    assert a1 is not None
    assert set(attr.AttributionInput.__dataclass_fields__) - {"item_id", "failed", "source_doc"} <= set(a1)
    assert a1["gold_in_chunk"] is True and a1["gold_in_candidates"] is True
    assert a1["gold_in_evidence"] is True and a1["gold_cited"] is True and a1["cited_outside_gold"] is False
    assert a1["unresolved_citations"] == 0 and a1["claim_missing_or_unsupported"] is False
    assert a1["should_abstain"] is False and a1["abstained"] is False and a1["judge_verdict"] == "pass"
    assert a1["failed"] is False
    a2 = by["a2"]
    assert (
        a2 is not None
        and a2["failed"] is True
        and a2["gold_in_evidence"] is True
        and a2["gold_cited"] is False
    )
    assert a2["claim_missing_or_unsupported"] is True and a2["judge_verdict"] == "fail"
    u1 = by["u1"]
    assert (
        u1 is not None and u1["should_abstain"] is True and u1["abstained"] is True and u1["failed"] is False
    )
    assert u1["gold_in_chunk"] is None  # no gold on an abstain item: not recorded, not False
    assert a1["gold_in_parsed"] is None  # not visible over HTTP: stays None


async def test_attribution_feeds_the_stage_table(tmp_path: pathlib.Path) -> None:
    res, _, _ = await run(tmp_path)
    side = tmp_path / f"{res.run_id}.json"
    inputs = attr.load_inputs(str(side))
    stages = {a.item_id: attr.attribute(a) for a in inputs}
    assert stages["a1"] is None and stages["a2"] == "utilization"  # right evidence, never cited
    assert stages["i1"] is not None  # an injection violation is a failure
    assert attr.stage_table(inputs)["utilization"][0] == 1
    raw = json.loads(side.read_text())
    assert raw["items"][0]["attribution"]["gold_in_evidence"] is True and raw["config"]["dataset"] == "v2"


async def test_gold_unknown_without_units(tmp_path: pathlib.Path) -> None:
    res, _, _ = await run(tmp_path, units=False)
    a1 = res.items[0].attribution
    assert a1 is not None and a1["gold_in_chunk"] is None and a1["gold_in_candidates"] is None
    assert "cite_sent_precision" not in res.items[0].metrics  # no gold targets, so no gold-based metric


def test_gold_targets_map_ranges_to_chunk_sentences() -> None:
    (t,), ok = gold_targets(ITEMS[1], {"d1": [C1]}, lambda d: UNITS)
    assert (
        ok and t.mapped and [(h.chunk_id, h.sentence_start, h.sentence_end) for h in t.hits] == [("c1", 1, 2)]
    )
    (bad,), ok2 = gold_targets(ITEMS[1], {"d1": [C1]}, lambda d: None)
    assert not ok2 and not bad.mapped


async def test_pipeline_error_is_scored_not_dropped(tmp_path: pathlib.Path) -> None:
    pipe = ScriptedPipeline(outputs(), raises={"a1"})
    h, _ = harness(pipe)
    res, _ = await run_suite_v2(
        [ITEMS[0]], h, name="toy", suite="full", split="all", report_dir=tmp_path, git_sha="abc1234"
    )
    (it,) = res.items
    assert it.error and "scripted failure" in it.error
    assert it.metrics["answer_pass"] == 0.0  # an empty answer entails nothing
    assert it.attribution is not None and it.attribution["failed"] is True


async def test_judges_run_concurrently(tmp_path: pathlib.Path) -> None:
    import asyncio

    pipe = ScriptedPipeline(outputs())
    h, fake = harness(pipe)
    live = peak = 0
    orig = fake.complete

    async def slow(req: ChatRequest) -> ChatResponse:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        try:
            return await orig(req)
        finally:
            live -= 1

    fake.complete = slow  # type: ignore[method-assign]
    await run_suite_v2(
        ITEMS, h, name="toy", suite="full", split="all", report_dir=tmp_path, git_sha="abc1234"
    )
    assert peak > 1  # calls overlap (bounded by gather_bounded), not one after another


# --- freeze -----------------------------------------------------------------------------------------------


async def test_freeze_file_shape(tmp_path: pathlib.Path) -> None:
    res, _, _ = await run(tmp_path)
    path = freeze_v2(res, "answer_v2", tmp_path)
    data = load_baseline(path)
    assert path.name == "answer_v2.json" and data["dataset"] == "v2" and data["clustered_by"] == "source_doc"
    ap = data["metrics"]["answer_pass"]
    assert set(ap) == {"mean", "n", "ci_low", "ci_high", "clusters"} and ap["n"] == 5
    assert ap["ci_low"] <= ap["mean"] <= ap["ci_high"]
    assert (
        data["per_stratum"]["single_span"]["n"] == 2
        and data["per_stratum"]["single_span"]["note"] == "diagnostic"
    )
    assert 0 <= data["per_stratum"]["single_span"]["metrics"]["answer_pass"] <= 1
    mi = data["mde_inputs"]["answer_pass"]
    assert set(mi) == {"n", "var", "design_effect", "clusters", "mde_80_unpaired"} and mi["n"] == 5
    a = data["attribution"]
    assert a["failed"] == 2  # a2 (utilization) and i1 (injection)
    assert a["by_stage"]["utilization"]["count"] == 1
    assert set(a["by_stage"]["utilization"]) == {"count", "share", "ci_low", "ci_high"}
    assert "gold_in_parsed" in a["not_recorded_among_failed"]
    assert set(data["per_item"]) == {"a1", "a2", "u1", "i1", "m1"}
    assert yaml.safe_load(path.read_text()) == data  # plain JSON, loadable as is


# --- the spend cap ----------------------------------------------------------------------------------------


async def test_spend_cap_stops_the_run(tmp_path: pathlib.Path) -> None:
    pipe = ScriptedPipeline(outputs())
    n_calls = 0

    async def paid() -> float:
        nonlocal n_calls
        n_calls = len(pipe.calls)
        return 0.02 * n_calls  # each answered turn costs $0.02

    h, _ = harness(pipe, paid)
    res, _ = await run_suite_v2(
        ITEMS, h, name="toy", suite="full", split="all", report_dir=tmp_path, git_sha="abc1234", max_usd=0.05
    )
    assert res.config["stopped"] == "budget"
    assert (
        len(res.items) == 3
    )  # stops once spend (0.06) >= cap (0.05), before the 4th item; those 3 are scored
    assert [q.item_id for q, _ in pipe.calls] == ["a1", "a2", "u1"]


async def test_judge_spend_stops_scoring(tmp_path: pathlib.Path) -> None:
    pipe = ScriptedPipeline(outputs())
    calls = {"n": 0}

    async def paid() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] <= 1 else 1.0  # cheap while answering, over the cap once judging starts

    h, _ = harness(pipe, paid)
    res, _ = await run_suite_v2(
        [ITEMS[0]],
        h,
        name="toy",
        suite="full",
        split="all",
        report_dir=tmp_path,
        git_sha="abc1234",
        max_usd=0.5,
    )
    assert res.config["stopped"] == "budget" and res.items == []  # an unscored item is not counted as scored


# --- v1 stays as it was -----------------------------------------------------------------------------------


async def test_v1_run_suite_unchanged(tmp_path: pathlib.Path) -> None:
    from tests.support.evalkit import item, out

    pipe = ScriptedPipeline({"toy:lookup": out(("Virginia.", [("c1", 0, 1)]), retrieved=["c1"])})
    run1, _ = await run_suite(
        [item(id="toy:lookup")], Harness(pipe, {"d1": [C1]}), name="toy", suite="full", split="all",
        report_dir=tmp_path, git_sha="abc1234",
    )  # fmt: skip
    assert run1.items[0].attribution is None and run1.items[0].stratum is None
    side = json.loads((tmp_path / f"{run1.run_id}.json").read_text())
    assert side["items"][0]["attribution"] is None and "dataset" not in side["config"]


def test_cli_v2_flags_parse() -> None:
    with pytest.raises(SystemExit):  # smoke is a v1 split: v2 refuses it before touching any service
        main(["--suite", "smoke", "--config", "x", "--pipeline", "m:s", "--dataset", "v2", "--no-db"])
    with pytest.raises(SystemExit):
        main(["--suite", "full", "--config", "x", "--pipeline", "m:s", "--dataset", "v3"])
