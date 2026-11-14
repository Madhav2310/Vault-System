"""S21.10 AC2: `python -m eval.gate compare` (what `make eval-gate` runs) exits non-zero on a
planted regression and zero on an identical candidate. Real subprocess (the exit code is the process exit
code). No DB, no model."""

import json
import os
import pathlib
import random
import subprocess
import sys

from tests.support.evalkit import REPO_ROOT

N = 100


def sidecar(name: str, ap: list[float], inj: list[float]) -> dict[str, object]:
    items = [
        {
            "item_id": f"i{k:03d}", "category": "v2", "split": "dev", "source_doc": f"doc{k % 25}",
            "stratum": "injection" if k >= 90 else "single_span",
            "metrics": {
                "answer_pass": ap[k], "support_rate": 0.9, "abstention_correct": 1.0,
                "citation_integrity": 1.0, "cost_usd": 0.01, "latency_ms": 4000.0,
                **({"injection_violation": inj[k]} if k >= 90 else {}),
            },
        }
        for k in range(N)
    ]  # fmt: skip
    return {
        "run_id": name,
        "name": name,
        "suite": "full",
        "split": "dev",
        "git_sha": "abc1234",
        "config": {},
        "items": items,
    }


def run_gate(base: pathlib.Path, cand: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "eval.gate", "compare", str(base), str(cand)],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, check=False,
    )  # fmt: skip


def test_planted_regression(tmp_path: pathlib.Path) -> None:
    ap = [1.0] * 70 + [0.0] * 30
    random.Random(3).shuffle(ap)
    base = tmp_path / "base.json"
    base.write_text(json.dumps(sidecar("base", ap, [0.0] * N)))

    same = tmp_path / "same.json"
    same.write_text(json.dumps(sidecar("same", ap, [0.0] * N)))
    ok = run_gate(base, same)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "GATE: PASS" in ok.stdout

    bad_ap = list(ap)
    for k in [k for k, v in enumerate(ap) if v == 1.0][:6]:  # 6-point answer_pass drop
        bad_ap[k] = 0.0
    inj = [0.0] * N
    inj[95] = 1.0  # one injection violation
    cand = tmp_path / "cand.json"
    cand.write_text(json.dumps(sidecar("cand", bad_ap, inj)))
    bad = run_gate(base, cand)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "FAIL         answer_pass:" in bad.stdout
    assert "FAIL         injection_violation:" in bad.stdout
    assert "GATE: FAIL" in bad.stdout
