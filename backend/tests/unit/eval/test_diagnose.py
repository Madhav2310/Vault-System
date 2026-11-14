from eval.diagnose import STAGES, Candidate, claim_records, claim_summary, classify, replay, select


def _order() -> list[Candidate]:
    # ranks 1-6 in document A, 7-9 in B
    return [Candidate(f"c{i}", "A" if i <= 6 else "B", 100) for i in range(1, 10)]


def test_select_global_top_then_each_documents_own_best() -> None:
    ids = [c.chunk_id for c in select(_order(), 2, 2)]
    assert ids == ["c1", "c2", "c7", "c8"]


def test_select_single_document_ignores_per_document() -> None:
    only_a = [c for c in _order() if c.doc == "A"]
    assert [c.chunk_id for c in select(only_a, 3, 6)] == ["c1", "c2", "c3"]


def test_replay_reports_gold_coverage_and_tokens() -> None:
    gold = [["c1"], ["c8"]]
    base = replay(_order(), gold, 2, 0)
    wide = replay(_order(), gold, 2, 2)
    assert (base.all_gold, wide.all_gold) == (False, True)
    assert wide.multi_doc and wide.tokens == 400 and wide.kept == 4


def test_classify_names_the_stage() -> None:
    kw = {"mapped": 1, "has_run": True, "abstained": False}
    assert classify(ranks=[None], selected=[False], **kw).startswith("retrieval miss")
    assert classify(ranks=[12], selected=[False], **kw).startswith("evidence cut")
    assert classify(ranks=[2], selected=[True], **kw).startswith("generation")
    assert classify(ranks=[2], selected=[True], **{**kw, "abstained": True}).startswith("abstained")
    assert classify(ranks=[], selected=[], **{**kw, "mapped": 0}).startswith("gold not locatable")


# --- per-claim evidence utilisation ---------------------------------------------------------------------

T_OK = {"extracted": True, "mapped": True, "reason": None, "quote": "Notice is six months."}
CLAIM = "Notice must be given six months before expiry."


def _facts(**over: object) -> dict:
    base: dict = {
        "claims": [CLAIM],
        "targets": [T_OK],
        "has_run": True,
        "ranks": [2],
        "selected": [True],
        "answer": CLAIM,
        "segments": [{"text": CLAIM, "passages": ["Notice is six months."]}],
        "judge": {
            "claims": [{"index": 0, "decision": True, "overruled": False, "quote": "Notice must be given"}],
            "segments": [{"index": 0, "decision": True}],
        },
        "gold_texts": ["Notice is six months."],
    }
    return base | over


def _no_claim() -> dict:
    return {"claims": [{"index": 0, "decision": False, "overruled": False, "quote": ""}], "segments": []}


def test_claim_that_passes_every_stage_has_no_failing_stage() -> None:
    (r,) = claim_records(**_facts())
    assert [r[s]["ok"] for s in STAGES] == [True] * 6
    assert r["first_failing_stage"] is None and r["attribution"] == "single-range"
    assert not r["possible_judge_error"] and not r["gold_suspect"]


def test_first_failing_stage_is_retrieved_when_gold_is_outside_the_pool() -> None:
    (r,) = claim_records(**_facts(ranks=[None], selected=[False], judge={"claims": [], "segments": []}))
    assert r["retrieved"]["ok"] is False and r["first_failing_stage"] == "retrieved"
    assert r["generated"]["ok"] is None and r["supported"]["ok"] is None


def test_unmapped_gold_fails_chunked_and_leaves_later_stages_unknown() -> None:
    t = {"extracted": True, "mapped": False, "reason": "no exact or fuzzy hit in any chunk", "quote": "x"}
    (r,) = claim_records(**_facts(targets=[t], ranks=[], selected=[]))
    assert r["extracted"]["ok"] and r["chunked"]["ok"] is False
    assert r["retrieved"]["ok"] is None and r["first_failing_stage"] == "chunked"


def test_missing_stored_run_makes_retrieval_and_selection_unknown_not_failed() -> None:
    (r,) = claim_records(**_facts(has_run=False, ranks=[], selected=[]))
    assert r["retrieved"]["ok"] is None and r["selected"]["ok"] is None
    assert "run" in r["retrieved"]["reason"]


def test_multi_range_item_is_marked_item_level_and_needs_every_range() -> None:
    f = _facts(targets=[T_OK, T_OK], ranks=[1, 30], selected=[True, False], gold_texts=["a", "b"])
    (r,) = claim_records(**f)
    assert r["attribution"] == "item-level (2 gold ranges)"
    assert r["selected"]["ok"] is False and r["first_failing_stage"] == "selected"


def test_supported_follows_the_segment_that_states_the_claim() -> None:
    f = _facts(
        claims=["A", "B"],
        segments=[
            {"text": "first stated", "passages": ["p1"]},
            {"text": "second stated", "passages": ["p2"]},
        ],
        judge={
            "claims": [
                {"index": 0, "decision": True, "overruled": False, "quote": "first stated"},
                {"index": 1, "decision": True, "overruled": False, "quote": "second stated"},
            ],
            "segments": [{"index": 0, "decision": True}, {"index": 1, "decision": False}],
        },
    )
    a, b = claim_records(**f)
    assert a["supported"]["ok"] is True and b["supported"]["ok"] is False
    assert b["first_failing_stage"] == "supported"


def test_supported_is_unknown_when_the_segment_cannot_be_determined() -> None:
    judge = {
        "claims": [{"index": 0, "decision": True, "overruled": False, "quote": ""}],
        "segments": [{"index": 0, "decision": True}],
    }
    (r,) = claim_records(**_facts(judge=judge))
    assert r["generated"]["ok"] is True and r["supported"]["ok"] is None


def test_cites_gold_chunk_is_reported_separately_from_support() -> None:
    (r,) = claim_records(**_facts(gold_texts=["completely different chunk"]))
    assert r["supported"]["ok"] is True and r["cites_gold_chunk"] is False


def test_not_entailed_claim_stated_near_verbatim_is_a_possible_judge_error() -> None:
    (r,) = claim_records(**_facts(judge=_no_claim()))
    assert r["generated"]["ok"] is False and r["possible_judge_error"] is True


def test_overruled_quote_is_a_possible_judge_error() -> None:
    judge = {"claims": [{"index": 0, "decision": False, "overruled": True, "quote": "zzz"}], "segments": []}
    (r,) = claim_records(**_facts(answer="Something else entirely.", judge=judge))
    assert r["possible_judge_error"] is True


def test_numbers_of_the_claim_present_in_the_answer_flag_a_possible_judge_error() -> None:
    f = _facts(
        claims=["The fee is 1,500 dollars per month payable in advance."],
        answer="Fees are payable in advance at 1,500 dollars a month.",
        judge=_no_claim(),
    )
    (r,) = claim_records(**f)
    assert r["possible_judge_error"] is True


def test_unrelated_answer_is_not_a_possible_judge_error() -> None:
    f = _facts(answer="The agreement is governed by Delaware law.", judge=_no_claim())
    (r,) = claim_records(**f)
    assert r["possible_judge_error"] is False


def test_very_long_claim_is_gold_suspect() -> None:
    (r,) = claim_records(**_facts(claims=[" ".join(["word"] * 60)]))
    assert r["gold_suspect"] is True


def test_summary_counts_stages_and_utilisation_overall_and_for_judge_confident_claims() -> None:
    def rec(stage: str | None, selected: bool | None, generated: bool | None, err: bool = False) -> dict:
        return {
            "first_failing_stage": stage,
            "selected": {"ok": selected},
            "generated": {"ok": generated},
            "possible_judge_error": err,
            "gold_suspect": False,
        }

    recs = [
        rec(None, True, True),
        rec("generated", True, False),
        rec("generated", True, False, err=True),
        rec("retrieved", False, None),
    ]
    text = "\n".join(claim_summary(recs))
    assert "utilisation, all claims: 1/3" in text
    assert "utilisation, judge-confident claims: 1/2" in text
    assert "generated" in text and "retrieved" in text
