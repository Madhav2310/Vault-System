"""S21.05: cross-model gold audit and apply_audit, offline (FakeProvider dry gateway, $0)."""

import asyncio
import pathlib
from decimal import Decimal
from typing import Any

import pytest
import yaml
from eval.build import apply_audit as ap
from eval.build import audit as au
from eval.judge import SpendGuard
from eval.schema import GoldItemV2

from app.llm.gateway import MemoryLedger

UNITS = {"d": ["Alpha is 5.", "Beta is 7.", "Gamma is 9."]}


def units_of(doc: str) -> list[str] | None:
    return UNITS.get(doc)


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


def _run(
    items: list[GoldItemV2], vault: dict[str, str], cap: str = "1000"
) -> tuple[au.AuditResult, SpendGuard]:
    ledger = MemoryLedger()
    gw = au._dry_gateway(ledger)
    guard = SpendGuard(ledger, Decimal(cap))
    return asyncio.run(au.run_audit(gw, items, vault, units_of, guard)), guard


def test_queue_shape() -> None:
    items = [
        _item("ok", ["Alpha is 5."]),  # both answers state it: unqueued
        _item("vault_bad", ["Beta is 7."], 1, 2),  # strong echoes the evidence, vault is wrong
        _item("gold_bad", ["Beta is 8."], 1, 2),  # gold claim contradicts the evidence: both fail
    ]
    vault = {"ok": "Alpha is 5.", "vault_bad": "Beta is 70.", "gold_bad": "Beta is 8."}
    res, _ = _run(items, vault)
    assert [a.item.id for a in res.queue] == ["vault_bad", "gold_bad"]
    doc = au.queue_document(res, spent=Decimal(0), cap=None)
    assert doc["audited"] == ["gold_bad", "ok", "vault_bad"]
    by = {q["id"]: q for q in doc["queue"]}
    q = by["vault_bad"]
    assert q["vault_answer"] == "Beta is 70." and q["strong_answer"] == "Beta is 7."
    assert q["gold_evidence"][0]["text"] == "Beta is 7." and q["gold_evidence"][0]["sentences"] == "1-2"
    assert q["failed"]["vault_claims"] == [0] and q["failed"]["strong_claims"] == []
    assert q["failed"]["disagree_claims"] == [0] and q["reasons"] == ["vault_fails_gold", "answers_disagree"]
    assert q["decision"] is None and q["fix"] is None
    g = by["gold_bad"]
    assert g["failed"]["vault_claims"] == [0] or g["failed"]["strong_claims"] == [0]
    assert "strong_fails_gold" in g["reasons"] and g["required_claims"] == ["Beta is 8."]
    # round trip through the file format
    assert yaml.safe_load(au.dump_queue(doc))["queue"] == doc["queue"]


def test_missing_vault_answer_and_units_skip() -> None:
    items = [
        _item("a", ["Alpha is 5."]),
        GoldItemV2.model_validate(
            {
                "id": "nodoc",
                "stratum": "single_span",
                "question": "Q?",
                "source_doc": "z",
                "answer_type": "answer",
                "required_claims": ["c"],
                "gold_evidence": [{"doc": "missing", "sentence_start": 0, "sentence_end": 1}],
            }
        ),
    ]
    res, _ = _run(items, {})
    assert res.skipped == {"nodoc": au.UNITS_UNAVAILABLE}
    # no Vault answer: that side is ungraded (counted in meta.vault_answers_missing), the strong answer passes
    assert not res.queue and res.audited[0].vault.answer is None and not res.audited[0].vault.graded
    assert au.queue_document(res, spent=Decimal(0), cap=None)["meta"]["vault_answers_missing"] == 1


def test_abstain_item() -> None:
    item = GoldItemV2.model_validate(
        {
            "id": "ab",
            "stratum": "unanswerable",
            "question": "Q?",
            "source_doc": "z",
            "answer_type": "abstain",
        }
    )
    res, _ = _run([item], {"ab": "The late fee is 2%."})
    assert [a.item.id for a in res.queue] == ["ab"]
    assert res.queue[0].vault.abstain_ok is False and res.queue[0].strong.abstain_ok is True
    assert res.queue[0].abstain_disagree
    res2, _ = _run([item], {"ab": "The documents do not contain this."})
    assert not res2.queue


def test_cap_enforced() -> None:
    items = [_item(f"i{n}", ["Alpha is 5."]) for n in range(6)]
    res, guard = _run(items, {i.id: "Alpha is 5." for i in items}, cap="0.0005")
    assert res.stopped and len(res.audited) < len(items)
    assert guard.spent <= Decimal("0.0005")
    assert all("spend cap" in r for r in res.skipped.values())


def test_render_review() -> None:
    res, _ = _run([_item("vault_bad", ["Beta is 7."], 1, 2)], {"vault_bad": "Beta is 70."})
    md = au.render_review(au.queue_document(res, spent=Decimal(0), cap=None))
    assert "`vault_bad`" in md and "VAULT: Beta is 70." in md and "STRONG: Beta is 7." in md


def test_vault_answers_formats(tmp_path: pathlib.Path) -> None:
    j = tmp_path / "a.jsonl"
    j.write_text('{"item_id": "x", "answer": "A"}\n{"item_id": "y", "answer": "B"}\n')
    assert au.load_vault_answers(j) == {"x": "A", "y": "B"}
    d = tmp_path / "a.json"
    d.write_text('{"answers": {"x": "A"}}')
    assert au.load_vault_answers(d) == {"x": "A"}
    s = tmp_path / "s.json"
    s.write_text('{"items": [{"item_id": "x", "metrics": {}}]}')
    with pytest.raises(SystemExit):
        au.load_vault_answers(s)


def test_cli_dry_run_and_cap_required(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    ans = tmp_path / "a.jsonl"
    ans.write_text("")
    assert au.main(["--answers", str(ans)]) == 2  # live without a cap is refused


# --- apply_audit -------------------------------------------------------------------------------------------

PART = """# header comment
items:
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
  audit_note: first note
- id: b
  stratum: single_span
  question: Qb?
  source_doc: doc_b
  answer_type: answer
  required_claims:
  - B claim.
  gold_evidence:
  - doc: d
    sentence_start: 1
    sentence_end: 2
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
- id: e
  stratum: single_span
  question: Qe?
  source_doc: doc_e
  answer_type: answer
  audit_status: draft
  required_claims:
  - E claim.
  gold_evidence:
  - doc: d
    sentence_start: 2
    sentence_end: 3
"""


def _queue(decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    claims = {"a": ["Old claim.", "Second."], "b": ["B claim."], "c": ["C claim."]}
    return {
        "audited": ["a", "b", "c"],
        "queue": [
            {
                "id": i,
                "required_claims": claims[i],
                "decision": None,
                "fix": None,
                "note": None,
                **decisions.get(i, {}),
            }
            for i in ("a", "c")
        ],
    }


def _ds(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "p.yaml").write_text(PART)
    return tmp_path


def _items(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in yaml.safe_load((root / "parts" / "p.yaml").read_text())["items"]}


def test_apply_refuses_undecided(tmp_path: pathlib.Path) -> None:
    root = _ds(tmp_path)
    q = tmp_path / "q.yaml"
    q.write_text(yaml.safe_dump(_queue({"a": {"decision": "system_wrong"}})))
    before = (root / "parts" / "p.yaml").read_text()
    assert ap.main(["--queue", str(q), "--dataset-dir", str(root)]) == 2
    assert (root / "parts" / "p.yaml").read_text() == before  # nothing written
    with pytest.raises(ap.ApplyError, match="no decision"):
        ap.apply(_queue({"a": {"decision": "system_wrong"}}), root)


def test_apply_decisions_idempotent(tmp_path: pathlib.Path) -> None:
    root = _ds(tmp_path)
    queue = _queue(
        {
            "a": {"decision": "gold_wrong", "fix": ["New claim."], "note": "typo"},
            "c": {"decision": "ambiguous", "note": "two readings"},
        }
    )
    q = tmp_path / "q.yaml"
    q.write_text(yaml.safe_dump(queue))
    assert ap.main(["--queue", str(q), "--dataset-dir", str(root)]) == 0
    text1 = (root / "parts" / "p.yaml").read_text()
    it = _items(root)
    assert it["a"]["required_claims"] == ["New claim."] and it["a"]["audit_status"] == "verified"
    assert it["a"]["audit_note"].startswith("first note [S21.05 audit: gold_wrong] typo")
    assert it["b"]["audit_status"] == "verified"  # unqueued: auto, key was absent
    assert it["c"]["audit_status"] == "dropped" and "two readings" in it["c"]["audit_note"]
    assert "audit_status" not in it["e"] or it["e"]["audit_status"] == "draft"  # not audited: untouched
    assert text1.startswith("# header comment\nitems:\n- id: a")  # formatting kept
    # idempotent: the second run changes nothing (the queue's claims are the old ones, `fix` matches now)
    assert ap.main(["--queue", str(q), "--dataset-dir", str(root)]) == 0
    assert (root / "parts" / "p.yaml").read_text() == text1


def test_apply_system_wrong_keeps_claims_and_validates_fix(tmp_path: pathlib.Path) -> None:
    root = _ds(tmp_path)
    res_texts, ch = ap.apply(
        _queue({"a": {"decision": "system_wrong"}, "c": {"decision": "system_wrong"}}), root
    )
    assert ch["a"].claims is None and ch["a"].status == "verified"
    assert "required_claims" in res_texts[root / "parts" / "p.yaml"]
    with pytest.raises(ap.ApplyError, match="needs `fix`"):
        ap.apply(_queue({"a": {"decision": "gold_incomplete"}, "c": {"decision": "system_wrong"}}), root)
    with pytest.raises(ap.ApplyError, match="needs a `note`"):
        ap.apply(_queue({"a": {"decision": "ambiguous"}, "c": {"decision": "system_wrong"}}), root)
    with pytest.raises(ap.ApplyError, match="unknown decision"):
        ap.apply(_queue({"a": {"decision": "maybe"}, "c": {"decision": "system_wrong"}}), root)
