"""The floor is picked from a fixture score/label table exactly as hand-computed."""

import pathlib

import pytest
from eval.calibrate_gate import Row, pick_floor, table, write_floor

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[3] / "config"

# 20 answerable items: scores 1.0 .. 19.0 plus one identifier match with score 0.1 (never abstained).
# 4 unanswerable items: 0.5, 1.5, 2.5, 30.0.
ROWS = [
    *(Row(f"a{i}", float(i), True) for i in range(1, 20)),
    Row("a-id", 0.1, True, bypass=True),
    Row("u1", 0.5, False),
    Row("u2", 1.5, False),
    Row("u3", 2.5, False),
    Row("u4", 30.0, False),
]


def test_pick_floor() -> None:
    # Abstain when score < floor. Answerable abstained at floor f: the scores 1..f-1, so (f - 1) / 20.
    #   floor 2.0 -> 1 item (1.0) = 0.05 (allowed); floor 2.5 -> 1.0 and 2.0 = 0.10 (too many).
    # Unanswerable abstained at floor 2.0: 0.5 and 1.5 = 2 / 4 = 0.5.
    chosen = pick_floor(ROWS)
    assert chosen is not None
    assert (chosen.floor, chosen.false_abstain, chosen.true_abstain) == (2.0, 0.05, 0.5)


def test_table_is_ascending_over_distinct_scores() -> None:
    pts = table(ROWS)
    floors = [p.floor for p in pts]
    assert floors == sorted(set(floors)) and 0.1 not in floors  # the bypassed row is not a candidate
    assert pts[0].false_abstain == 0.0 and pts[0].true_abstain == 0.0  # the lowest score: nothing is below it
    at = {p.floor: p for p in pts}
    assert (at[2.5].false_abstain, at[2.5].true_abstain) == (0.10, 0.5)
    assert (at[30.0].false_abstain, at[30.0].true_abstain) == (0.95, 0.75)


def test_no_scores_picks_nothing() -> None:
    assert pick_floor([Row("x", None, True)]) is None
    assert pick_floor([]) is None


def test_write_floor_touches_only_the_gate(tmp_path: pathlib.Path) -> None:
    src = (CONFIG_DIR / "retrieval@2.yaml").read_text(encoding="utf-8")
    path = tmp_path / "retrieval@2.yaml"
    path.write_text(src, encoding="utf-8")
    write_floor(path, 2.0)
    out = path.read_text(encoding="utf-8")
    assert "  floor: 2" in out and "  enabled: false" not in out
    assert "expansion:\n  enabled: true" in out  # expansion.enabled untouched
    changed = [(a, b) for a, b in zip(src.splitlines(), out.splitlines(), strict=True) if a != b]
    assert len(changed) == 2
    with pytest.raises(SystemExit):
        write_floor(_no_gate(tmp_path), 1.0)


def _no_gate(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "x.yaml"
    p.write_text("version: x\n", encoding="utf-8")
    return p
