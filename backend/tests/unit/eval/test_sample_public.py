"""Seeded public-strata sampler (classification rules, determinism, output validity)."""

import collections
import pathlib
import sys

import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT / "eval" / "build"))
import sample_public as sp  # noqa: E402
from eval.loader import validate_items  # noqa: E402

PUBLIC = _ROOT / "eval" / "datasets" / "vault_eval" / "parts" / "public.yaml"
HAVE_SOURCES = (sp.DEFAULT_SOURCES / "fb_questions.jsonl").exists()


def _cands(n_docs: int = 12, per_doc: int = 6) -> list[sp.Cand]:
    return [
        sp.Cand(
            key=f"k{d}_{i}",
            stratum="single_span",
            src="cuad",
            doc=f"doc{d}",
            pdf=None,
            question="q",
            claims=["c"],
            quotes=[("x", None)],
        )
        for d in range(n_docs)
        for i in range(per_doc)
    ]


def _sample(seed: int, used: collections.Counter | None = None) -> list[str]:
    kept = sp.sample_stratum(
        _cands(),
        15,
        seed,
        "single_span",
        used if used is not None else collections.Counter(),
        lambda c: [(0, 1)],
    )
    return [c.key for c, _ in kept]


def test_seeded() -> None:
    assert _sample(1) == _sample(1)
    assert _sample(1) != _sample(2)


def test_per_doc_cap_and_spread() -> None:
    used: collections.Counter = collections.Counter()
    keys = _sample(3, used)
    docs = collections.Counter(k.split("_")[0] for k in keys)
    assert max(docs.values()) <= sp.MAX_PER_DOC
    assert len(docs) == 12  # round-robin spreads over every document before reusing one


def test_global_cap_across_strata() -> None:
    used: collections.Counter = collections.Counter({f"doc{d}": 3 for d in range(11)})
    keys = _sample(3, used)
    assert {k.split("_")[0] for k in keys} == {"k11"} and len(keys) == 3


def _fb_row(question: str, ans: str, r: str | None = None, qt: str = "domain-relevant", ev: int = 1) -> dict:
    return {
        "financebench_id": "fb1",
        "doc_name": "ACME_2022_10K",
        "question": question,
        "question_type": qt,
        "question_reasoning": r,
        "answer": ans,
        "justification": "",
        "evidence": [{"evidence_text": "Revenue 1,577 1,373", "evidence_page_num": 3}] * ev,
    }


def test_fb_rules() -> None:
    lookup = _fb_row("What is the FY2018 capital expenditure for ACME (USD millions)?", "$1577.00")
    assert sp.fb_stratum(lookup) == "table_lookup"
    assert sp.fb_stratum(_fb_row("What is the FY2019 fixed asset turnover ratio?", "24.26")) == "numeric_calc"
    assert (
        sp.fb_stratum(_fb_row("By how much did revenue change from 2017 to 2018?", "$200")) == "numeric_calc"
    )
    assert sp.fb_stratum(_fb_row("What was the average of inventories?", "$4.2 billion")) == "numeric_calc"
    assert sp.fb_stratum(_fb_row("Operating margin in FY2020?", "12.5%")) == "numeric_calc"
    shrank = _fb_row(
        "Which segment shrank the most?", "The consumer segment shrank the most.", qt="novel-generated"
    )
    assert sp.fb_stratum(shrank) == "single_span"
    logical = _fb_row("Is ACME capital intensive?", "No.", "Logical reasoning (based on numerical reasoning)")
    assert sp.fb_stratum(logical) is None
    assert sp.fb_stratum(_fb_row("Q?", "Yes. A. B. C.", qt="novel-generated", ev=3)) is None


def test_fb_table_lookup_needs_verbatim_row() -> None:
    texts = ["capital expenditures", "purchases of ppe (1,577) (1,373)", "operating margin was 18.75 percent"]
    st = sp.Stream([3, 3, 3], ["paragraph", "table", "paragraph"], texts)
    # figure present verbatim in a row: lookup (1577.00 -> 1,577 matches, bounded)
    assert sp.figure_units(st, 0, 3, sp.figure_forms("$1577.00")) == [1]
    # 8.7 must not match inside 18.75; no verbatim figure means the candidate is a derived value
    assert sp.figure_units(st, 0, 3, sp.figure_forms("$8.70")) == []
    # the demotion path: a table_lookup candidate carries a numeric_calc twin
    cand = sp.fb_cand(
        _fb_row("What is the FY2018 capex?", "$8.70"), "table_lookup", lambda n: pathlib.Path(n)
    )
    assert cand is not None and cand.demote is not None
    twin = cand.demote()
    assert twin is not None and twin.stratum == "numeric_calc" and not twin.narrow
    # map_cand rejects the lookup candidate with the reason run() demotes on
    cand.quotes = [("purchases of ppe (1,577) (1,373)", 3)]
    assert sp.map_cand(cand, st) == "figure_not_in_row"


def test_qasper_rules() -> None:
    ext = {"unanswerable": False, "extractive_spans": ["a b", "c d"], "yes_no": None, "free_form_answer": ""}
    one = {"unanswerable": False, "extractive_spans": [], "yes_no": None, "free_form_answer": "It uses BERT."}
    assert sp.qasper_stratum(one, ["Intro", "Results"]) == "multi_hop"
    assert sp.qasper_stratum(ext, ["Results", "Results"]) == "multi_span"
    assert sp.qasper_stratum(one, ["Results"]) == "single_span"
    assert sp.qasper_stratum({**one, "unanswerable": True}, ["Results"]) is None
    assert sp.qasper_stratum(one, [None]) is None


def test_cuad_rules() -> None:
    cond = "Either party may terminate on 30 days written notice unless a breach is cured."
    assert sp.cuad_stratum("Termination For Convenience", [cond]) == "conditional"
    assert sp.cuad_stratum("Termination For Convenience", ["The sky is blue."]) is None
    assert sp.cuad_stratum("Parties", ["A Inc.", "B LLC"]) == "multi_span"
    assert sp.cuad_stratum("Governing Law", ["x"]) == "single_span"
    assert sp.cuad_stratum("Governing Law", ["x", "y"]) is None
    assert all(len(v) >= 3 for v in sp.CUAD_QUESTIONS.values())


def test_alternates_and_claims() -> None:
    assert "1,577" in sp.number_alternates("$1577.00") or "1,577.00" in sp.number_alternates("$1577.00")
    assert "$1.2 billion" in sp.number_alternates("1200 million", None) or "1.2 billion" in " ".join(
        sp.number_alternates("1,200 million")
    )
    assert "2020-05-18" in sp.date_alternates("This agreement is dated May 18, 2020.")
    made = sp.cuad_claims("Governing Law", ["governed by the laws of the State of New York."])
    assert made and "New York" in made[0][0]


def test_locate_and_stream() -> None:
    texts = ["the company was founded in 1999.", "revenue was 1,577 million.", "see note 4."]
    st = sp.Stream([1, 1, 2], ["text", "table", "text"], texts)
    h = sp.locate(st, "Revenue was 1,577 million.")
    assert h and (h.lo, h.hi) == (1, 2)
    h2 = sp.locate(st, "founded in 1999. revenue was 1,577")
    assert h2 and (h2.lo, h2.hi) == (0, 2)
    assert sp.locate(st, "absent quote that is not here at all") is None


@pytest.mark.skipif(not PUBLIC.exists(), reason="parts/public.yaml not generated (needs eval/sources)")
def test_output_validates_and_caps() -> None:
    items = yaml.safe_load(PUBLIC.read_text())["items"]
    validate_items(items)  # includes the 3-per-document cap
    assert all(i["gold_evidence"] for i in items)
    assert max(collections.Counter(i["source_doc"] for i in items).values()) <= sp.MAX_PER_DOC


@pytest.mark.skipif(not HAVE_SOURCES, reason="eval/sources not present (set VAULT_EVAL_SOURCES)")
def test_corpus_candidates_classified() -> None:
    cands = sp.build_candidates(sp.Sources(sp.DEFAULT_SOURCES, sp.DEFAULT_CACHE, offline=True))
    assert {c.stratum for c in cands} >= {"table_lookup", "numeric_calc", "multi_hop", "conditional"}
