"""PDF tables: pdfplumber `find_tables` (ruled tables) → pipe-table rows built from the
glyph-mapped words in each cell, so table text obeys invariant I1 too. The only synthetic characters are
spaces, newlines and the ` | ` cell separators. Words inside a table are removed from the paragraph flow (no
duplicated text). Columns that are empty in every row (spacer columns in financial statements) are dropped.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.ingestion.extract.glyphs import Line, _expand

SEP, EDGE_L, EDGE_R = " | ", "| ", " |"


@dataclass
class TableBlock:
    top: float
    bottom: float
    rows: list[Line]
    n_cols: int

    @property
    def header(self) -> str:
        return self.rows[0].text


def _center_in(w: dict[str, Any], box: Sequence[float]) -> bool:
    cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
    return bool(box[0] <= cx <= box[2] and box[1] <= cy <= box[3])


def _row_line(cells: list[list[dict[str, Any]]], top: float, bottom: float) -> Line:
    text: list[str] = [EDGE_L]
    src: list[int] = [-1] * len(EDGE_L)
    sizes: list[float] = []
    for c, words in enumerate(cells):
        if c:
            text.append(SEP)
            src.extend([-1] * len(SEP))
        for i, w in enumerate(sorted(words, key=lambda w: (round(w["top"]), w["x0"]))):
            if i:
                text.append(" ")
                src.append(-1)
            t, s = _expand(w)
            text.append(t)
            src.extend(s)
            sizes.extend(float(ch.get("size", 0.0)) for ch in w["chars"])
    text.append(EDGE_R)
    src.extend([-1] * len(EDGE_R))
    flat = [w for cell in cells for w in cell]
    return Line(
        "".join(text),
        src,
        min((w["x0"] for w in flat), default=0.0),
        max((w["x1"] for w in flat), default=0.0),
        top,
        bottom,
        statistics.median(sizes) if sizes else 0.0,
        False,
        flat,
    )


def extract_tables(page: Any, words: list[dict[str, Any]]) -> tuple[list[TableBlock], list[dict[str, Any]]]:
    """Tables on an unrotated page, and the words left for the paragraph flow."""
    used: set[int] = set()
    blocks: list[TableBlock] = []
    for t in page.find_tables():
        grid = [[cell for cell in row.cells] for row in t.rows]
        if len(grid) < 2 or max(len(r) for r in grid) < 2:
            continue
        n_cols = max(len(r) for r in grid)
        cells: list[list[list[dict[str, Any]]]] = [[[] for _ in range(n_cols)] for _ in grid]
        mine: set[int] = set()
        for wi, w in enumerate(words):
            if wi in used or not _center_in(w, t.bbox):
                continue
            for r, row in enumerate(grid):
                for c, box in enumerate(row):
                    if box is not None and _center_in(w, box):
                        cells[r][c].append(w)
                        mine.add(wi)
                        break
                else:
                    continue
                break
        keep = [c for c in range(n_cols) if any(cells[r][c] for r in range(len(grid)))]
        rows = [
            _row_line([cells[r][c] for c in keep], float(t.rows[r].bbox[1]), float(t.rows[r].bbox[3]))
            for r in range(len(grid))
            if any(cells[r][c] for c in keep)
        ]
        if len(rows) >= 2 and len(keep) >= 2:
            used |= mine
            blocks.append(TableBlock(float(t.bbox[1]), float(t.bbox[3]), rows, len(keep)))
    remaining = [w for i, w in enumerate(words) if i not in used]
    return blocks, remaining
