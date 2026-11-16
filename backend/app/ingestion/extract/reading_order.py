"""Reading-order safety.

Content-stream order (`use_text_flow=True`) is the default and is right for most pages. Three repairs:
1. **Gutter split**: a line whose words have a horizontal gap wider than `GAP_EM` font sizes is split, so two
   columns drawn in step cannot fuse into one line.
2. **Column repair**: if the line starts form 2 or 3 well-separated x-clusters (1-D k-means,
   silhouette-chosen; never a fixed mid-split) *and* the sequence keeps jumping between them, lines
   are re-ordered by column within horizontal bands separated by full-width lines (titles, captions).
3. **Footnotes**: small-font lines in the bottom 20% of the page become separate `footnote` lines.
"""

import itertools
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from app.ingestion.extract.glyphs import Line

GAP_EM = 1.5
FOOTNOTE_SIZE_RATIO = 0.85
FOOTNOTE_ZONE = 0.8  # lines whose top is below 80% of the page height
MIN_COLUMN_SEPARATION = 0.25  # cluster centres at least a quarter page width apart
MIN_COLUMN_SHARE = 0.15  # each column holds at least this share of the lines
MIN_SILHOUETTE = 0.6
SPANNING = 0.6  # a line wider than this share of the page spans the columns
MIN_COLUMN_LINE_WIDTH = 0.25  # prose columns have long lines; table number columns do not
SILHOUETTE_SAMPLE = 200
JUMP_SHARE = 0.25  # column changes per column line needed before re-ordering


@dataclass
class Ordered:
    body: list[Line]
    footnotes: list[Line]
    flags: list[str]


def _line_from(words: Sequence[dict[str, object]], proto: Line) -> Line:
    from app.ingestion.extract.glyphs import build_lines

    (ln,) = build_lines(list(words))
    return ln if ln.words else proto


def split_gutters(lines: Sequence[Line]) -> tuple[list[Line], int]:
    out: list[Line] = []
    splits = 0
    for ln in lines:
        groups: list[list[dict[str, object]]] = [[]]
        for i, w in enumerate(ln.words):
            if i and float(w["x0"]) - float(ln.words[i - 1]["x1"]) > GAP_EM * max(ln.size, 1.0):
                groups.append([])
            groups[-1].append(w)
        if len(groups) == 1:
            out.append(ln)
            continue
        splits += len(groups) - 1
        out.extend(_line_from(g, ln) for g in groups)
    return out, splits


def _kmeans_1d(xs: Sequence[float], k: int, iters: int = 30) -> list[int]:
    srt = sorted(xs)
    centres = [srt[int((i + 0.5) * len(srt) / k)] for i in range(k)]  # deterministic quantile init
    labels = [0] * len(xs)
    for _ in range(iters):
        labels = [min(range(k), key=lambda c: abs(x - centres[c])) for x in xs]
        new = [
            statistics.fmean([x for x, lab in zip(xs, labels, strict=True) if lab == c] or [centres[c]])
            for c in range(k)
        ]
        if new == centres:
            break
        centres = new
    return labels


def _silhouette(xs: Sequence[float], labels: Sequence[int]) -> float:
    if len(xs) > SILHOUETTE_SAMPLE:  # evenly spaced sample keeps this O(sample^2) on table-heavy pages
        step = len(xs) / SILHOUETTE_SAMPLE
        idx = [int(i * step) for i in range(SILHOUETTE_SAMPLE)]
        xs, labels = [xs[i] for i in idx], [labels[i] for i in idx]
    ks = set(labels)
    if len(ks) < 2:
        return 0.0
    total = 0.0
    for i, x in enumerate(xs):
        own = [abs(x - y) for j, y in enumerate(xs) if labels[j] == labels[i] and j != i]
        a = statistics.fmean(own) if own else 0.0
        b = min(
            statistics.fmean([abs(x - y) for j, y in enumerate(xs) if labels[j] == k])
            for k in ks
            if k != labels[i]
        )
        total += (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return total / len(xs)


def columns(lines: Sequence[Line], page_width: float) -> list[int] | None:
    """Column label per line (0 = leftmost) when the page has 2 or 3 real columns, else None."""
    narrow = [i for i, ln in enumerate(lines) if (ln.x1 - ln.x0) < SPANNING * page_width]
    if len(narrow) < 6:
        return None
    xs = [lines[i].x0 for i in narrow]
    best: tuple[float, list[int]] | None = None
    for k in (2, 3):
        if len(set(round(x) for x in xs)) < k:
            continue
        labels = _kmeans_1d(xs, k)
        s = _silhouette(xs, labels)
        centres = sorted(
            statistics.fmean([x for x, lab in zip(xs, labels, strict=True) if lab == c]) for c in set(labels)
        )
        shares = [labels.count(c) / len(labels) for c in set(labels)]
        widths = [
            statistics.median(
                [lines[narrow[j]].x1 - lines[narrow[j]].x0 for j, lab in enumerate(labels) if lab == c]
            )
            for c in set(labels)
        ]
        prose = min(widths) >= MIN_COLUMN_LINE_WIDTH * page_width
        separated = all(b - a >= MIN_COLUMN_SEPARATION * page_width for a, b in itertools.pairwise(centres))
        if (
            s >= MIN_SILHOUETTE
            and separated
            and prose
            and min(shares) >= MIN_COLUMN_SHARE
            and (best is None or s > best[0])
        ):
            best = (s, labels)
    if best is None:
        return None
    # relabel clusters left-to-right
    labels = best[1]
    order = sorted(
        set(labels), key=lambda c: statistics.fmean([xs[j] for j, lab in enumerate(labels) if lab == c])
    )
    rank = {c: r for r, c in enumerate(order)}
    out = [-1] * len(lines)  # -1 = spanning line
    for idx, lab in zip(narrow, labels, strict=True):
        out[idx] = rank[lab]
    return out


def _jumps(labels: Sequence[int]) -> int:
    seq = [lab for lab in labels if lab >= 0]
    return sum(1 for a, b in itertools.pairwise(seq) if a != b)


def repair_columns(lines: Sequence[Line], labels: Sequence[int]) -> list[Line]:
    """Within bands separated by spanning lines, emit column 0 top-down, then column 1, ..."""
    out: list[Line] = []
    band: list[tuple[int, Line]] = []

    def flush() -> None:
        out.extend(ln for _, ln in sorted(band, key=lambda t: (t[0], t[1].top)))
        band.clear()

    for ln, lab in zip(lines, labels, strict=True):
        if lab < 0:
            flush()
            out.append(ln)
        else:
            band.append((lab, ln))
    flush()
    return out


def order_page(lines: Sequence[Line], page_width: float, page_height: float) -> Ordered:
    flags: list[str] = []
    sizes = [ln.size for ln in lines for _ in ln.text]
    body_size = statistics.median(sizes) if sizes else 0.0
    body: list[Line] = []
    foot: list[Line] = []
    for ln in lines:
        if body_size and ln.size < FOOTNOTE_SIZE_RATIO * body_size and ln.top > FOOTNOTE_ZONE * page_height:
            foot.append(ln)
        else:
            body.append(ln)
    if foot:
        flags.append("footnotes_separated")
    body, splits = split_gutters(body)
    labels = columns(body, page_width)
    if labels is not None:
        flags.append("multi_column")
        n_cols = len({lab for lab in labels if lab >= 0})
        in_cols = sum(1 for lab in labels if lab >= 0)
        # Interleaved columns jump on most lines; figure text or a side note jumps a few times only, and
        # "repairing" those would splice them into body paragraphs.
        if _jumps(labels) > max(n_cols, JUMP_SHARE * in_cols):
            flags.append("reading_order_suspect")
            body = repair_columns(body, labels)
            flags.append("reading_order_repaired")
    if splits and "reading_order_repaired" not in flags:
        flags.append("gutter_split")
    return Ordered(body, foot, flags)
