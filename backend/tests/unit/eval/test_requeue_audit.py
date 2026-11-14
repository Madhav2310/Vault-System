"""S21.05b: requeue of the stored audit queue with the current judges, offline (dry gateway, $0)."""

import asyncio
import pathlib
from decimal import Decimal
from typing import Any

import yaml
from eval.build import apply_audit as ap
from eval.build import audit as au
from eval.build import requeue_audit as rq
from eval.judge import SpendGuard
from eval.schema import GoldItemV2

from app.llm.gateway import MemoryLedger

UNITS = {"d": ["Alpha is 5.", "Beta is 7.", "Gamma is 9."]}


def _item(iid: str, claims: list[str], start: int = 0, end: int = 1, **kw: Any) -> GoldItemV2:
    base: dict[str, Any] = {
        "id": iid,
        "stratum": "single_span",
        "question": f"Question {iid}?",
        "source_doc": f"doc_{iid}",
        "answer_type": "answer",
        "required_claims": claims,
        "gold_evidence": [{"doc": "d", "sentence_start": start, "sentence_end": end}],
    }
    base.update(kw)
    return GoldItemV2.model_validate(base)


def _entry(item: GoldItemV2, vault: str, strong: str, **kw: Any) -> dict[str, Any]:
    text = " ".join(" ".join(UNITS["d"][g.sentence_start : g.sentence_end]) for g in item.gold_evidence)
    return {
        "id": item.id,
        "stratum": item.stratum,
        "source_doc": item.source_doc,
        "question": item.question,
        "answer_type": item.answer_type,
        "required_claims": list(item.required_claims),
        "gold_evidence": [{"doc": "d", "sentences": "x", "text": text}],
        "vault_answer": vault,
        "strong_answer": strong,
        "reasons": ["vault_fails_gold", "answers_disagree"],  # as judged by the old (over-strict) judge
        "failed": {},
        "errors": [],
        "decision": None,
        "fix": None,
        "note": None,
        **kw,
    }


ITEMS = [
    _item("both_pass", ["Alpha is 5."]),
    _item("vault_only", ["Beta is 7."], 1, 2),
    _item("strong_only", ["Beta is 7."], 1, 2),
    _item("gold_bad", ["Beta is 8."], 1, 2),
    _item("partial", ["Alpha is 5.", "Beta is 8."], 0, 2),
    _item("filled", ["Alpha is 5."]),
    _item("rca", ["Gamma is 10."], 2, 3),
    GoldItemV2.model_validate(
        {
            "id": "abst",
            "stratum": "unanswerable",
            "question": "Q?",
            "source_doc": "z",
            "answer_type": "abstain",
        }
    ),
]
DECLINE = "The documents do not contain this information."


def _queue() -> dict[str, Any]:
    by = {i.id: i for i in ITEMS}
    return {
        "meta": {"audited": 9, "queued": len(ITEMS)},
        "audited": [*by, "unqueued"],
        "skipped": {},
        "queue": [
            _entry(by["both_pass"], "Alpha is 5.", "Alpha is 5."),
            _entry(by["vault_only"], "Beta is 70.", "Beta is 7."),
            _entry(by["strong_only"], "Beta is 7.", "Beta is 70."),
            _entry(by["gold_bad"], "Beta is 7.", "Beta is 7."),
            _entry(by["partial"], "Alpha is 5. Beta is 7.", "Alpha is 5. Beta is 7."),
            _entry(by["filled"], "Alpha is 5.", "Alpha is 5.", decision="ambiguous", note="two readings"),
            _entry(by["rca"], "Gamma is 9.", "Gamma is 9."),
            _entry(by["abst"], "The late fee is 2%.", DECLINE),
        ],
    }


def _run(queue: dict[str, Any], cap: str = "1000") -> tuple[rq.RequeueResult, Any, SpendGuard]:
    ledger = MemoryLedger()
    gw = au._dry_gateway(ledger)
    guard = SpendGuard(ledger, Decimal(cap))
    rca = {"rca": ("CONFIDENT", "claim overstates the passage")}
    res = asyncio.run(rq.requeue(gw, queue, {i.id: i for i in ITEMS}, guard, rca))
    return res, gw, guard


def _kept(res: rq.RequeueResult) -> dict[str, dict[str, Any]]:
    return {q["id"]: q for q in res.document["queue"]}


def test_drops_both_pass_keeps_disagreements_and_filled() -> None:
    res, _, _ = _run(_queue())
    kept = _kept(res)
    assert "both_pass" not in kept and res.dropped == ["both_pass"]
    assert kept["vault_only"]["reasons"] == ["vault_fails_gold", "answers_disagree"]
    assert kept["strong_only"]["reasons"] == ["strong_fails_gold", "answers_disagree"]
    assert kept["abst"]["reasons"] == ["vault_fails_gold", "answers_disagree"]
    assert kept["filled"]["decision"] == "ambiguous" and kept["filled"]["note"] == "two readings"
    assert "unqueued" in res.document["audited"]  # the audited list is untouched
    m = res.document["meta"]
    assert m["queued"] == len(kept) and m["requeue"]["before"]["queued"] == 8
    assert m["requeue"]["after"]["queued"] == 7 and m["requeue"]["dropped"] == 1


def test_no_new_answering_calls() -> None:
    res, gw, guard = _run(_queue())
    schemas = [set((r.output_schema or {}).get("properties", {})) for r in gw.chat.requests]
    assert schemas and not any("answer" in s for s in schemas)  # judges only, no answer generation
    assert guard.spent > 0 and res.spent == guard.spent


def test_proposed_fields() -> None:
    res, _, _ = _run(_queue())
    kept = _kept(res)
    assert all(
        "proposed_decision" in q and "proposed_reason" in q and "proposed_fix" in q for q in kept.values()
    )
    assert all(q["decision"] in (None, "ambiguous") for q in kept.values())  # a human decides
    v = kept["vault_only"]
    assert v["proposed_decision"] == "system_wrong" and v["proposed_fix"] is None
    assert "\n" not in v["proposed_reason"]
    s = kept["strong_only"]
    assert s["proposed_decision"] == "system_wrong" and "strong" in s["proposed_reason"]
    g = kept["gold_bad"]  # both fail the only claim
    assert g["proposed_decision"] == "ambiguous" and g["proposed_fix"] is None
    p = kept["partial"]  # both fail c1 only: keep the claim they state
    assert p["proposed_decision"] == "gold_incomplete" and p["proposed_fix"] == ["Alpha is 5."]
    r = kept["rca"]  # both fail; listed CONFIDENT in the RCA
    assert "RCA" in r["proposed_reason"] and r["rca_gold_audit"] == "CONFIDENT"
    a = kept["abst"]
    assert a["proposed_decision"] == "system_wrong"  # strong declines, Vault answers


def test_cap_keeps_old_entries() -> None:
    res, _, guard = _run(_queue(), cap="0.0001")
    assert res.stopped and guard.spent <= Decimal("0.0001")
    assert len(res.document["queue"]) == 8 and res.dropped == []


def test_rca_ids(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "rca.md"
    f.write_text(
        "## 6. x\n| `not_me` | a | b |\n## 7. Gold to send\n\n| item | issue | conf |\n|---|---|---|\n"
        "| `x-1` | claims wrong | CONFIDENT |\n| `y_2` | maybe | GUESS |\n\n"
        "## 8. other\n| `z` | q | CONFIDENT |\n"
    )
    assert rq.rca_gold_ids(f) == {"x-1": ("CONFIDENT", "claims wrong"), "y_2": ("GUESS", "maybe")}


def test_cli_requires_cap_and_refuses_overwrite(tmp_path: pathlib.Path) -> None:
    q = tmp_path / "q.yaml"
    q.write_text(au.dump_queue(_queue()))
    assert rq.main(["--queue", str(q)]) == 2  # live without a cap
    assert rq.main(["--queue", str(q), "--dry-run", "--out", str(tmp_path / "o.yaml")]) == 0


def test_render_review_compact() -> None:
    res, _, _ = _run(_queue())
    md = au.render_review(res.document)
    block = md.split("**")  # per item: header, Q, GOLD, answers, proposed
    assert "`partial`" in md and "PROPOSED: gold_incomplete" in md and "Alpha is 5." in md
    skim = md.split("## Proposed system_wrong")[1]
    assert "- `vault_only`" in skim and "`partial`" not in skim  # system_wrong proposals are a skim list
    assert "EVIDENCE" not in md and len(block) > 4
    first = md.split("\n\n")[2]
    assert len(first.strip().splitlines()) <= 5


# --- apply_audit --accept-proposed -----------------------------------------------------------------------


PART = """items:
- id: a
  stratum: single_span
  question: Qa?
  source_doc: doc_a
  answer_type: answer
  audit_status: draft
  required_claims:
  - Old claim.
  - Second.
  gold_evidence:
  - doc: d
    sentence_start: 0
    sentence_end: 1
- id: c
  stratum: single_span
  question: Qc?
  source_doc: doc_c
  answer_type: answer
  audit_status: draft
  required_claims:
  - C claim.
  gold_evidence:
  - doc: d
    sentence_start: 2
    sentence_end: 3
"""


def _aq(**over: Any) -> dict[str, Any]:
    def e(i: str, claims: list[str], **kw: Any) -> dict[str, Any]:
        return {"id": i, "required_claims": claims, "decision": None, "fix": None, "note": None, **kw}

    return {
        "audited": ["a", "c"],
        "queue": [
            e(
                "a",
                ["Old claim.", "Second."],
                proposed_decision="gold_incomplete",
                proposed_fix=["Old claim."],
                proposed_reason="both fail c1",
            ),
            e("c", ["C claim."], proposed_decision="system_wrong", proposed_reason="vault misses", **over),
        ],
    }


def test_apply_refuses_undecided_unless_accept_proposed(tmp_path: pathlib.Path) -> None:
    root = tmp_path
    (root / "parts").mkdir()
    (root / "parts" / "p.yaml").write_text(PART)
    q = tmp_path / "q.yaml"
    q.write_text(yaml.safe_dump(_aq()))
    before = (root / "parts" / "p.yaml").read_text()
    assert ap.main(["--queue", str(q), "--dataset-dir", str(root)]) == 2  # proposals are not decisions
    assert (root / "parts" / "p.yaml").read_text() == before
    assert ap.main(["--queue", str(q), "--dataset-dir", str(root), "--accept-proposed"]) == 0
    items = {r["id"]: r for r in yaml.safe_load((root / "parts" / "p.yaml").read_text())["items"]}
    assert items["a"]["required_claims"] == ["Old claim."] and items["a"]["audit_status"] == "verified"
    assert items["c"]["audit_status"] == "verified"
    assert yaml.safe_load(q.read_text())["queue"][0]["decision"] is None  # the queue file is not rewritten


def test_accept_proposed_keeps_human_decision_and_skips_missing_proposal() -> None:
    q = _aq()
    q["queue"][1]["decision"] = "ambiguous"
    q["queue"][1]["note"] = "mine"
    out = ap.accept_proposed(q)
    assert out["queue"][1]["decision"] == "ambiguous" and out["queue"][1]["note"] == "mine"
    assert out["queue"][0]["decision"] == "gold_incomplete" and out["queue"][0]["fix"] == ["Old claim."]
    assert out["queue"][0]["note"] == "both fail c1"
    q2 = _aq()
    del q2["queue"][1]["proposed_decision"]
    assert ap.accept_proposed(q2)["queue"][1]["decision"] is None  # no proposal: stays undecided
    raw = {"a": {"required_claims": ["Old claim.", "Second."]}, "c": {"required_claims": ["C claim."]}}
    assert any("no decision" in p for p in ap.check_queue(ap.accept_proposed(q2), raw))
