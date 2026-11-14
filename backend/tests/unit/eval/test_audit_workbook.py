# ruff: noqa: E501
import pathlib

import pytest
import yaml
from eval.build import audit_workbook as wb
from eval.judge import dump_labels
from eval.labels.schema import LabeledPair, LabeledSegment, LabelFile


def queue_entry(iid: str, proposed: str, fix: list[str] | None = None) -> dict:
    return {
        "id": iid, "stratum": "single_span", "source_doc": "d", "question": "q", "required_claims": ["a", "b"],
        "proposed_decision": proposed, "proposed_fix": fix, "proposed_reason": "because",
        "decision": None, "fix": None, "note": None,
    }  # fmt: skip


@pytest.fixture()
def files(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, pathlib.Path]:
    queue = {
        "meta": {},
        "queue": [
            queue_entry("i1", "gold_incomplete", ["a", "b", "c"]),
            queue_entry("i2", "system_wrong"),
            queue_entry("i3", "system_wrong"),
        ],
    }
    (tmp_path / "q.yaml").write_text("# header\n" + yaml.safe_dump(queue))
    pair = LabeledPair(
        pair_id="p1", item_id="x", stratum="unanswerable", source_doc="d", split="dev", question="q", answer="a",
        segments=[LabeledSegment(text="t", passages=["p"])], required_claims=["c0"], claim_labels=[None],
        support_labels=[None],
    )  # fmt: skip
    dump_labels(LabelFile(pairs=[pair]), str(tmp_path / "l.yaml"))
    monkeypatch.setattr(wb, "QUEUE", tmp_path / "q.yaml")
    monkeypatch.setattr(wb, "LABELS", tmp_path / "l.yaml")
    monkeypatch.setattr(wb, "WORKBOOK", tmp_path / "AUDIT.md")
    return tmp_path / "q.yaml", tmp_path / "l.yaml"


def write(text: str) -> None:
    wb.WORKBOOK.write_text(text)


BASE = """### A01 | i1 | single_span
>>> DECISION: {d1}
>>> FIX:
{fix}
>>> NOTE: {note}

>>> ACCEPT_ALL_SKIM: {sw}

### S01 | i2 | single_span
>>> DECISION: {d2}
>>> FIX:
>>> NOTE:

### S02 | i3 | single_span
>>> DECISION:
>>> FIX:
>>> NOTE:

### B001 | p1 | unanswerable | split=dev
>>> c0: {c0}
>>> s0: n
>>> abstain (did it behave so?): yes
>>> notes: unclear
"""


def fill(**kw: str) -> str:
    base = {"d1": "accept", "fix": "", "note": "", "sw": "yes", "d2": "", "c0": "y"}
    return BASE.format(**{**base, **kw})


def test_accept_and_skim_switch_write_decisions_and_labels(files: tuple[pathlib.Path, pathlib.Path]) -> None:
    write(fill())
    problems, stat = wb.apply(check=False)
    assert problems == [] and stat["gold_decided"] == 3
    q = yaml.safe_load(files[0].read_text())["queue"]
    assert [x["decision"] for x in q] == ["gold_incomplete", "system_wrong", "system_wrong"]
    assert q[0]["fix"] == ["a", "b", "c"] and files[0].read_text().startswith("# header")
    p = yaml.safe_load(files[1].read_text())["pairs"][0]
    assert p["claim_labels"] == [True] and p["support_labels"] == [False] and p["abstain_label"] is True


def test_item_decision_wins_over_the_switch(files: tuple[pathlib.Path, pathlib.Path]) -> None:
    write(fill(d2="ambiguous", note="two readings"))
    wb.apply(check=False)
    q = yaml.safe_load(files[0].read_text())["queue"]
    assert q[1]["decision"] == "ambiguous" and q[2]["decision"] == "system_wrong"


def test_gold_fix_needs_a_list_and_nothing_is_written_on_problems(
    files: tuple[pathlib.Path, pathlib.Path],
) -> None:
    write(fill(d1="gold_wrong"))
    before = files[0].read_text()
    problems, _ = wb.apply(check=False)
    assert any("needs a FIX list" in p for p in problems) and files[0].read_text() == before


def test_fix_list_is_read_from_dash_lines(files: tuple[pathlib.Path, pathlib.Path]) -> None:
    write(fill(d1="gold_wrong", fix="- first claim\n- second claim"))
    wb.apply(check=False)
    assert yaml.safe_load(files[0].read_text())["queue"][0]["fix"] == ["first claim", "second claim"]


def test_unreviewed_items_stay_empty_and_unknown_decisions_are_reported(
    files: tuple[pathlib.Path, pathlib.Path],
) -> None:
    write(fill(d1="", sw="no"))
    _, stat = wb.apply(check=True)
    assert stat["gold_empty"] == 3
    write(fill(d1="wrongish"))
    problems, _ = wb.apply(check=True)
    assert any("unknown decision" in p for p in problems)
