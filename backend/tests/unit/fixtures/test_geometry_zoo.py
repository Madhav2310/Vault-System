import json

import pdfplumber

from tests.fixtures import make_fixtures as mf

ZOO = mf.GEOMETRY_DIR


def test_zoo_complete() -> None:
    assert len(mf.GEOMETRY_CASES) == 9
    assert sorted(p.stem for p in ZOO.glob("*.pdf")) == sorted(mf.GEOMETRY_CASES)
    for case in mf.GEOMETRY_CASES:
        snap = json.loads((ZOO / f"{case}.json").read_text())
        assert snap["case"] == case
        assert snap["file"] == f"{case}.pdf"
        assert snap["failure_modes"]
        with pdfplumber.open(ZOO / snap["file"]) as pdf:
            assert len(pdf.pages) == len(snap["pages"]), case
        assert snap["targets"], case
        for t in snap["targets"] + snap["excluded"]:
            assert t["text"].strip()
            assert t["boxes"], (case, t["text"])
            for b in t["boxes"]:
                assert 1 <= b["page"] <= len(snap["pages"])
                assert b["x0"] < b["x1"] and b["top"] < b["bottom"], (case, b)
                if t.get("reason") != "outside_cropbox":
                    assert all(0.0 <= b[k] <= 1.0 for k in ("x0", "top", "x1", "bottom")), (case, b)
