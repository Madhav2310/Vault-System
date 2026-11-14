from eval.diagnose import Candidate, classify, replay, select


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
