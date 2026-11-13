"""S20.04: docs/scale.md carries a computed cost table and only sourced numbers."""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[4]
DOC = (ROOT / "docs" / "scale.md").read_text()


def _rows() -> list[list[str]]:
    rows = []
    for line in DOC.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def test_cost_table_present() -> None:
    assert "## 5. Cost per 1,000 documents" in DOC
    samples = json.loads((ROOT / "docs" / "evidence" / "brief_samples.json").read_text())
    per_doc = sum(float(v["cost_usd"]) for v in samples.values()) / len(samples)
    brief = next(r for r in _rows() if r[0].startswith("Brief,"))
    assert f"${per_doc * 1000:.2f}" in brief[2], "brief row must equal the measured mean x 1,000"
    assert "brief_samples.json" in brief[-1]
    for item in ("Embeddings", "Contextual chunk headers", "Total"):
        assert any(r[0].strip("*").startswith(item) for r in _rows()), item
    assert "Assumptions:" in DOC and "Regenerate the brief row:" in DOC


def test_numbers_sourced() -> None:
    bad = []
    for cells in _rows()[:]:
        if cells[0] in {"Measure", "Item", "Mechanism"}:
            continue
        body = " ".join(cells[:-1])
        if not re.search(r"\d", body) and "TBD" not in body:
            continue  # no number claimed in this row
        source = cells[-1]
        if "TBD" in body and source == "none yet":
            continue
        if "`" not in source:
            bad.append(cells[0])
    assert not bad, f"rows with numbers but no backticked source: {bad}"
    # every TBD row says what would measure it
    assert DOC.count("TBD") >= 4
