"""docs/operations.md carries a computed cost table and only sourced numbers."""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[4]
DOC = (ROOT / "docs" / "operations.md").read_text()
SAMPLES = ROOT / "eval" / "baselines" / "brief_samples.json"


def _rows(skip_tables: tuple[str, ...] = ()) -> list[list[str]]:
    """Table rows of the document, minus the tables whose first header cell is in `skip_tables`."""
    rows: list[list[str]] = []
    skipping = False
    previous_was_table = False
    for line in DOC.splitlines():
        if not line.startswith("|"):
            previous_was_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not previous_was_table:
            skipping = cells[0] in skip_tables
        previous_was_table = True
        if skipping or all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def test_cost_table_present() -> None:
    assert "## 4. Cost" in DOC
    samples = json.loads(SAMPLES.read_text())
    per_doc = sum(float(v["cost_usd"]) for v in samples.values()) / len(samples)
    brief = next(r for r in _rows() if r[0].startswith("Brief,"))
    assert f"${per_doc * 1000:.2f}" in brief[2], "brief row must equal the measured mean x 1,000"
    assert "brief_samples.json" in brief[-1]
    for item in ("Embeddings", "Contextual chunk headers", "Total"):
        assert any(r[0].strip("*").startswith(item) for r in _rows()), item
    assert "Assumptions:" in DOC and "Regenerate the brief row:" in DOC


def test_numbers_sourced() -> None:
    """A row that states a number names where it comes from; a TBD row says what would measure it."""
    bad = []
    for cells in _rows(skip_tables=("Time",)):
        if cells[0] in {"Measure", "Item", "Mechanism", "Work", "Setting"}:
            continue
        if len(cells) < 3:
            continue
        body = " ".join(cells[:-1])
        if not re.search(r"\d", body) and "TBD" not in body:
            continue  # no number claimed in this row
        source = cells[-1]
        if "TBD" in body and source == "none yet":
            continue
        if not source or ("`" not in source and "arithmetic" not in source):
            bad.append(cells[0])
    assert not bad, f"rows with numbers but no source: {bad}"
    assert DOC.count("TBD") >= 4


def test_samples_file_has_facts() -> None:
    samples = json.loads(SAMPLES.read_text())
    facts = [f for v in samples.values() for f in v["facts"]]
    assert sum(f["status"] == "verified" for f in facts) == 56 and len(facts) == 58
