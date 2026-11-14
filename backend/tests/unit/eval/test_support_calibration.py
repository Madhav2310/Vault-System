"""Tooling: kappa maths and a dry run of the calibration (no network, no spend)."""

import math

import pytest
from eval.support_calibration import cohens_kappa, main


def test_kappa() -> None:
    assert cohens_kappa([(True, True), (False, False)]) == 1.0
    assert cohens_kappa([(True, False), (False, True)]) == -1.0
    assert math.isnan(cohens_kappa([]))
    assert cohens_kappa([(True, True)] * 4) == 1.0  # chance agreement 1: defined as 1.0


def test_live_run_needs_a_cap(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--n", "3"]) == 2
    assert "--max-usd" in capsys.readouterr().err


def test_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--n", "6", "--dry-run"])
    out = capsys.readouterr().out
    assert "support_check@1 vs eval_judge_faithfulness@1: n=6" in out and "kappa=" in out
    assert code in (0, 1)
