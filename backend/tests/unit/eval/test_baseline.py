"""S2.12 AC4: compare_to_baseline fails when a metric drops beyond tolerance."""

import datetime as dt
import pathlib
import uuid

import pytest
from eval import baseline as bl
from eval.report import ItemResult, RunResult

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def _run(values: dict[str, list[float | None]], errors: int = 0) -> RunResult:
    """values: metric -> per-item values (None = metric not defined for that item)."""
    n = len(next(iter(values.values())))
    r = RunResult(uuid.uuid4(), "cfg", "full", "all", "abc1234", NOW, NOW)
    for k in range(n):
        m = {name: v[k] for name, v in values.items() if v[k] is not None}
        r.items.append(ItemResult(f"i{k}", "lookup", "train", m, "boom" if k < errors else None))
    return r


def _frozen(run: RunResult, tmp_path: pathlib.Path) -> dict[str, object]:
    return bl.load_baseline(bl.freeze(run, "base", tmp_path))


def test_regression_detected(tmp_path: pathlib.Path) -> None:
    base = _run({"recall@8": [1.0, 1.0, 0.0, 1.0], "cite_chunk_precision": [1.0, 0.5, 1.0, 0.5]})
    b = _frozen(base, tmp_path)

    same = bl.compare_to_baseline(
        _run({"recall@8": [1.0, 1.0, 0.0, 1.0], "cite_chunk_precision": [1.0, 0.5, 1.0, 0.5]}), b
    )
    assert same.ok and same.regressions == []
    same.raise_on_regression()

    # recall 0.75 -> 0.50 (drop 0.25 > 0.02): fails; precision unchanged
    worse = bl.compare_to_baseline(
        _run({"recall@8": [1.0, 0.0, 0.0, 1.0], "cite_chunk_precision": [1.0, 0.5, 1.0, 0.5]}), b
    )
    assert not worse.ok
    assert [r.metric for r in worse.regressions] == ["recall@8"]
    assert worse.regressions[0].baseline == 0.75 and worse.regressions[0].current == 0.5
    with pytest.raises(bl.BaselineRegression, match=r"recall@8: 0.7500 -> 0.5000"):
        worse.raise_on_regression()

    # a drop inside the tolerance passes; the same drop fails under a tighter per-metric tolerance
    small = _run({"recall@8": [1.0, 1.0, 0.0, 1.0], "cite_chunk_precision": [1.0, 0.5, 0.9, 0.5]})
    assert bl.compare_to_baseline(small, b, 0.05).ok  # precision 0.75 -> 0.725
    assert [
        r.metric for r in bl.compare_to_baseline(small, b, {"cite_chunk_precision": 0.01}).regressions
    ] == ["cite_chunk_precision"]

    # gains are not regressions
    assert bl.compare_to_baseline(_run({"recall@8": [1.0] * 4, "cite_chunk_precision": [1.0] * 4}), b).ok


def test_regression_edge_cases(tmp_path: pathlib.Path) -> None:
    base = _run({"recall@8": [1.0, 1.0], "latency_ms": [100.0, 100.0], "gold_unmapped": [0.0, 0.0]})
    b = _frozen(base, tmp_path)
    # a metric that vanished is a regression, not a silent pass
    missing = bl.compare_to_baseline(_run({"recall@8": [1.0, 1.0], "latency_ms": [100.0, 100.0]}), b)
    assert [r.metric for r in missing.regressions] == ["gold_unmapped"] and missing.regressions[
        0
    ].current is None
    # lower-is-better metrics regress upward, relative tolerance
    slow = bl.compare_to_baseline(
        _run({"recall@8": [1.0, 1.0], "latency_ms": [130.0, 130.0], "gold_unmapped": [0.0, 0.0]}), b, 0.1
    )
    assert [r.metric for r in slow.regressions] == ["latency_ms"]
    # any newly unmapped gold regresses from a clean baseline
    unm = bl.compare_to_baseline(
        _run({"recall@8": [1.0, 1.0], "latency_ms": [100.0, 100.0], "gold_unmapped": [1.0, 0.0]}), b, 0.1
    )
    assert [r.metric for r in unm.regressions] == ["gold_unmapped"]
    # a different item set is not comparable
    with pytest.raises(ValueError, match="different item sets"):
        bl.compare_to_baseline(_run({"recall@8": [1.0, 1.0, 1.0]}), b)
    # newly errored items regress even when the metrics look fine
    errs = bl.compare_to_baseline(
        _run({"recall@8": [1.0, 1.0], "latency_ms": [100.0, 100.0], "gold_unmapped": [0.0, 0.0]}, errors=1), b
    )
    assert [r.metric for r in errs.regressions] == ["errored_items"]


def test_freeze_roundtrip(tmp_path: pathlib.Path) -> None:
    run = _run({"recall@8": [1.0, 0.0]})
    path = bl.freeze(run, "smoke_v1", tmp_path)
    assert path == tmp_path / "smoke_v1.json"
    data = bl.load_baseline(path)
    assert data["metrics"] == {"recall@8": {"mean": 0.5, "n": 2}}
    assert data["per_item"] == {"i0": {"recall@8": 1.0}, "i1": {"recall@8": 0.0}}
    assert bl.compare_to_baseline(run, path).ok  # path form is accepted
