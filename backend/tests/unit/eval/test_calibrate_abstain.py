"""S22.07 calibration tooling on a synthetic features file (no model, no database)."""

import json
import pathlib

import pytest
from eval import calibrate_abstain as C

from app.chat import abstain as A


def _write(path: pathlib.Path) -> None:
    rows = []
    for split in ("train", "dev"):
        for i in range(60):
            ans = i % 3 != 0
            rows.append(
                {
                    "item_id": f"{split}{i}",
                    "stratum": "single_span"
                    if ans
                    else ("unanswerable", "false_premise", "versioned")[i % 9 // 3],
                    "split": split,
                    "top": (2.0 if ans else -2.0) + 0.05 * (i % 5),
                    "margin": (1.0 if ans else 0.2) + 0.01 * i,
                    "not_found": 0 if ans else 2,
                    "unsupported": 0.0 if ans else 0.5,
                }
            )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_calibrate_end_to_end(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # type: ignore[no-untyped-def]
    run, out = tmp_path / "r.abstain.jsonl", tmp_path / "abstain@1.yaml"
    _write(run)
    monkeypatch.setattr(C, "CONFIG", out)
    assert C.main(["--split", "dev", "--run", str(run), "--write"]) == 0
    text = capsys.readouterr().out
    assert "false_refusals=0.0000" in text and "abstain_recall=1.0000" in text and "PASS" in text
    cfg = A.load_config(out)
    assert cfg.gate is not None and cfg.final is not None
    # same input, same file
    out2 = tmp_path / "again.yaml"
    monkeypatch.setattr(C, "CONFIG", out2)
    assert C.main(["--split", "dev", "--run", str(run), "--write"]) == 0
    assert out.read_text() == out2.read_text()


def test_write_refused_off_dev_and_missing_input(tmp_path: pathlib.Path) -> None:
    assert C.main(["--split", "test", "--run", str(tmp_path / "x"), "--write"]) == 2
    assert C.main(["--split", "dev", "--run", str(tmp_path / "missing.jsonl")]) == 2


def test_missing_final_features_fit_gate_only(tmp_path: pathlib.Path) -> None:
    run = tmp_path / "r.abstain.jsonl"
    _write(run)
    rows = [
        C.Row(r.item_id, r.stratum, r.split, {**r.x, "not_found": None, "unsupported": None})
        for r in C.load_rows(run)
    ]
    cfg = C.calibrate(rows, 3)
    assert cfg is not None and cfg.gate is not None and cfg.final is None
