"""S0.21 spike: geometry transforms (CropBox offset, /Rotate, ligature expansion), research 05 §3.

Cases (exit 0 only if every check in the case passes):
  cropbox    word box with the CropBox offset removed vs the pdfium render of the page (IoU >= 0.8)
  rotate90   natural reading order + transformed box vs render, for both rotate-90 variants
  rotate270  same for /Rotate 270
  ligature   QASPER 1910.03814: expanded ligature chars map to their glyph box; paragraph round-trip

Ground truth for the render checks is pixel ink: the target line is the only red text on its page,
so its rendered bounding box is found from red pixels, independently of any text extractor.
The target strings contain ascenders and descenders so ink height ~ font-box height.

Run: cd backend && uv run --frozen --group eval python ../scripts/spikes/geometry_spike.py --case cropbox
Overlays: docs/evidence/spike_geometry/<case>_p<page>.png (green = our box, blue = ink, orange = naive)
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from pdfplumber.utils.text import WordExtractor
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ZOO = ROOT / "backend" / "tests" / "fixtures" / "geometry"
SOURCES = ROOT / "eval" / "sources"
EVIDENCE = ROOT / "docs" / "evidence" / "spike_geometry"
EXTRACT_CFG = {"use_text_flow": True, "x_tolerance_ratio": 0.15}  # production config (design §6.4)
IOU_MIN = 0.8
RENDER_SCALE = 2.0

Box = tuple[float, float, float, float]  # x0, top, x1, bottom


# --------------------------------------------------------------------------- transforms
def rotate_box(b: Box, r: int, w: float, h: float) -> Box:
    """Box in an upright frame (w x h, top-left origin) -> the frame rotated clockwise by r degrees."""
    x0, t, x1, bt = b
    if r == 0:
        return b
    if r == 90:
        return (h - bt, x0, h - t, x1)
    if r == 180:
        return (w - x1, h - bt, w - x0, h - t)
    if r == 270:
        return (t, w - x1, bt, w - x0)
    raise ValueError(r)


def unrotate_box(b: Box, r: int, w: float, h: float) -> Box:
    """Inverse of rotate_box: (w, h) are the *upright* frame's dimensions."""
    x0, t, x1, bt = b
    if r == 0:
        return b
    if r == 90:
        return (t, h - x1, bt, h - x0)
    if r == 180:
        return (w - x1, h - bt, w - x0, h - t)
    if r == 270:
        return (w - bt, x0, w - t, x1)
    raise ValueError(r)


@dataclass
class PageGeo:
    """MediaBox/CropBox in PDF user space (bottom-left origin) and /Rotate."""

    media: tuple[float, float, float, float]
    crop: tuple[float, float, float, float]
    rotation: int

    @classmethod
    def of(cls, page: Any) -> PageGeo:
        return cls(tuple(page.page_obj.mediabox), tuple(page.page_obj.cropbox), int(page.rotation) % 360)

    @property
    def media_wh(self) -> tuple[float, float]:
        return self.media[2] - self.media[0], self.media[3] - self.media[1]

    @property
    def crop_wh(self) -> tuple[float, float]:
        return self.crop[2] - self.crop[0], self.crop[3] - self.crop[1]

    def display_wh(self) -> tuple[float, float]:
        w, h = self.crop_wh
        return (h, w) if self.rotation in (90, 270) else (w, h)

    def plumber_to_unrotated(self, b: Box) -> Box:
        """pdfplumber coords (MediaBox-relative, /Rotate applied) -> unrotated MediaBox, top-left (F6)."""
        return unrotate_box(b, self.rotation, *self.media_wh)

    def normalize(self, b: Box) -> Box | None:
        """Unrotated MediaBox top-left box -> normalized rendered page: CropBox offset removed (F5),
        /Rotate applied, [0,1], clamped; None when fully outside the CropBox."""
        dx = self.crop[0] - self.media[0]
        dy = self.media[3] - self.crop[3]  # CropBox top, measured from the MediaBox top
        x0, t, x1, bt = b[0] - dx, b[1] - dy, b[2] - dx, b[3] - dy
        cw, ch = self.crop_wh
        if x1 <= 0 or bt <= 0 or x0 >= cw or t >= ch:
            return None
        x0, t, x1, bt = rotate_box((x0, t, x1, bt), self.rotation, cw, ch)
        dw, dh = self.display_wh()
        clamp = lambda v: min(1.0, max(0.0, v))  # noqa: E731
        return clamp(x0 / dw), clamp(t / dh), clamp(x1 / dw), clamp(bt / dh)


def text_rotation(chars: list[dict[str, Any]]) -> int:
    """Dominant clockwise rotation (0/90/180/270) of glyph baselines in pdfplumber's display space.
    The char matrix is in a y-up device frame: baseline direction = (a, b)."""
    votes: dict[int, int] = {}
    for c in chars:
        if not c["text"].strip():
            continue
        a, b = c["matrix"][0], c["matrix"][1]
        ang = round(math.degrees(math.atan2(-b, a)) / 90) * 90 % 360  # clockwise, y-down
        votes[ang] = votes.get(ang, 0) + 1
    return max(votes, key=lambda k: votes[k]) if votes else 0


# --------------------------------------------------------------------------- glyph stream (I1)
@dataclass
class Stream:
    text: str
    src: list[int | None]  # per character: glyph index, or None for synthetic space/newline
    glyph_boxes: list[Box | None]  # per glyph: normalized display box
    glyph_line: list[int]  # per glyph: line number in the stream
    rot: int  # rotation the extraction ran in


def build_stream(page: Any, *, derotate: str = "text") -> Stream:
    """Character stream built from glyphs. derotate='text' extracts in the frame where glyphs are
    upright (dominant char direction); 'page' always undoes /Rotate (research 05 §3.3 as written);
    'none' extracts in display space."""
    geo = PageGeo.of(page)
    chars = page.dedupe_chars().chars
    dw, dh = page.width, page.height  # pdfplumber display frame (MediaBox, rotated)
    rot = {"text": text_rotation(chars), "page": geo.rotation, "none": 0}[derotate]
    uw, uh = (dh, dw) if rot in (90, 270) else (dw, dh)  # upright-frame dims
    work = []
    for i, c in enumerate(chars):
        x0, t, x1, b = unrotate_box((c["x0"], c["top"], c["x1"], c["bottom"]), rot, uw, uh)
        work.append({**c, "x0": x0, "x1": x1, "top": t, "bottom": b, "doctop": t, "upright": True, "_g": i})
    words = WordExtractor(**EXTRACT_CFG).extract_words(work, return_chars=True)

    text: list[str] = []
    src: list[int | None] = []
    glyph_line = [-1] * len(chars)
    line, prev = 0, None
    for w in words:
        if prev is not None:
            new_line = abs(w["top"] - prev["top"]) > 0.5 * min(
                w["bottom"] - w["top"], prev["bottom"] - prev["top"]
            )
            line += new_line
            text.append("\n" if new_line else " ")
            src.append(None)
        for c in w["chars"]:
            exp = unicodedata.normalize("NFKC", c["text"])  # ligatures/multi-char glyphs (F7, F8)
            text.extend(exp)
            src.extend([c["_g"]] * len(exp))
            glyph_line[c["_g"]] = line
        prev = w
    boxes = [
        geo.normalize(geo.plumber_to_unrotated((c["x0"], c["top"], c["x1"], c["bottom"]))) for c in chars
    ]
    return Stream("".join(text), src, boxes, glyph_line, rot)


def merge_line_rects(stream: Stream, glyphs: list[int], pad_pt: float = 0.0, page: Any = None) -> list[Box]:
    """Glyph boxes -> one rectangle per line run (gap < 1.5 x mean char width), research 05 §3.4."""
    rects: list[Box] = []
    by_line: dict[int, list[Box]] = {}
    for g in dict.fromkeys(glyphs):
        b = stream.glyph_boxes[g]
        if b is not None:
            by_line.setdefault(stream.glyph_line[g], []).append(b)
    for boxes in by_line.values():
        horizontal = (boxes[-1][0] - boxes[0][0]) ** 2 >= (boxes[-1][1] - boxes[0][1]) ** 2
        key = 0 if horizontal else 1
        boxes.sort(key=lambda b: b[key])
        avg = sum((b[2] - b[0]) if horizontal else (b[3] - b[1]) for b in boxes) / len(boxes)
        cur = list(boxes[0])
        for b in boxes[1:]:
            gap = (b[0] - cur[2]) if horizontal else (b[1] - cur[3])
            if gap < 1.5 * avg:
                cur = [min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3])]
            else:
                rects.append(tuple(cur))  # type: ignore[arg-type]
                cur = list(b)
        rects.append(tuple(cur))  # type: ignore[arg-type]
    if pad_pt and page is not None:
        px, py = pad_pt / float(page.width), pad_pt / float(page.height)
        rects = [(r[0] - px, r[1] - py, r[2] + px, r[3] + py) for r in rects]
    return rects


def union(boxes: list[Box]) -> Box:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


# --------------------------------------------------------------------------- rendering
def render(pdf_path: Path, page_index: int) -> Image.Image:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return doc[page_index].render(scale=RENDER_SCALE).to_pil().convert("RGB")
    finally:
        doc.close()


def red_ink_box(img: Image.Image) -> Box:
    w, h = img.size
    px = img.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if r > 140 and g < 110 and b < 110:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise RuntimeError("no red ink found in render")
    return min(xs) / w, min(ys) / h, (max(xs) + 1) / w, (max(ys) + 1) / h


def save_overlay(img: Image.Image, name: str, boxes: dict[str, Box | None]) -> Path:
    colors = {"ours": (0, 170, 0), "ink": (0, 90, 255), "naive": (255, 140, 0)}
    out = img.copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    for k, b in boxes.items():
        if b is not None:
            d.rectangle([b[0] * w, b[1] * h, b[2] * w, b[3] * h], outline=colors[k], width=3)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / f"{name}.png"
    out.save(path, optimize=True)
    return path


# --------------------------------------------------------------------------- cases
def find_glyphs(stream: Stream, target: str) -> list[int]:
    i = stream.text.find(target)
    if i < 0:
        return []
    return [g for g in stream.src[i : i + len(target)] if g is not None]


def render_check(pdf: Path, page_no: int, target: str, label: str, naive: Box | None = None) -> bool:
    with pdfplumber.open(pdf) as doc:
        page = doc.pages[page_no - 1]
        stream = build_stream(page)
        glyphs = find_glyphs(stream, target)
        order_ok = bool(glyphs)
        print(
            f"  [{label}] text rot={stream.rot} page rot={page.rotation} target found in stream: {order_ok}"
        )
        if not order_ok:
            print(f"  [{label}] stream head: {stream.text[:120]!r}")
            return False
        rects = merge_line_rects(stream, glyphs)
    ours = union(rects)
    img = render(pdf, page_no - 1)
    ink = red_ink_box(img)
    score = iou(ours, ink)
    path = save_overlay(img, label, {"ours": ours, "ink": ink, "naive": naive})
    extra = f"  naive IoU={iou(naive, ink):.3f}" if naive else ""
    print(
        f"  [{label}] rects={len(rects)} IoU={score:.3f} (min {IOU_MIN}){extra}"
        f"  overlay={path.relative_to(ROOT)}"
    )
    return score >= IOU_MIN


def case_cropbox() -> bool:
    pdf = ZOO / "cropbox_offset.pdf"
    target = json.loads((ZOO / "cropbox_offset.json").read_text())["targets"][0]["text"]
    with pdfplumber.open(pdf) as doc:
        page = doc.pages[0]
        geo = PageGeo.of(page)
        print(f"  MediaBox={geo.media} CropBox={geo.crop} pdfplumber page size={page.width}x{page.height}")
        words = page.extract_words(**EXTRACT_CFG)
        first = target.split()[0]
        w = next(w for w in words if w["text"] == first)
        last = next(w for w in words if w["text"] == target.split()[-1])
        # naive: MediaBox-relative coords normalized by pdfplumber's page size (the F5 bug)
        naive = (
            w["x0"] / page.width,
            w["top"] / page.height,
            last["x1"] / page.width,
            last["bottom"] / page.height,
        )
        margin = next(w for w in words if w["text"] == "Margin")
        dropped = geo.normalize(
            geo.plumber_to_unrotated((margin["x0"], margin["top"], margin["x1"], margin["bottom"]))
        )
        print(f"  word outside CropBox dropped: {dropped is None}")
    return render_check(pdf, 1, target, "cropbox_p1", naive) and dropped is None


def case_rotate(rot: int) -> bool:
    name = f"rotate_{rot}"
    pdf = ZOO / f"{name}.pdf"
    snap = json.loads((ZOO / f"{name}.json").read_text())
    ok = True
    for t in (t for t in snap["targets"] if t.get("color") == "red"):
        pg = t["boxes"][0]["page"]
        with pdfplumber.open(pdf) as doc:
            page = doc.pages[pg - 1]
            plain = page.dedupe_chars().extract_text(**EXTRACT_CFG)
            as_written = build_stream(page, derotate="page").text
            print(f"  [{t['variant']}] production extract_text first 40 chars: {plain[:40]!r}")
            print(f"  [{t['variant']}] research-05 'always de-rotate by /Rotate' finds the line: "
                  f"{t['text'] in as_written}")  # fmt: skip
            lines = [x["text"] for x in snap["targets"] if x["variant"] == t["variant"]]
            in_order = "\n".join(lines) in build_stream(page).text
            print(f"  [{t['variant']}] both lines in natural reading order: {in_order}")
            ok &= in_order
        ok &= render_check(pdf, pg, t["text"], f"{name}_p{pg}_{t['variant']}")
    return ok


REF = re.compile(r"\s*(BIBREF|TABREF|FIGREF|SECREF|INLINEFORM|DISPLAYFORM)\d*")
QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})


def norm_expected(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", REF.sub("", s)).translate(QUOTES)).strip()


def comparison_view(stream: Stream) -> tuple[str, list[int]]:
    """Stream -> comparison string + index map. Drops line-end hyphen+newline (F9), bracketed
    citation markers, collapses whitespace. original_text itself is never changed."""
    t = stream.text
    keep = [True] * len(t)
    for m in re.finditer(r"-\n", t):
        keep[m.start()] = keep[m.start() + 1] = False
    for m in re.finditer(r" ?\[\d+(?:,\s?\d+)*\]", t):
        for k in range(m.start(), m.end()):
            keep[k] = False
    out, idx = [], []
    for k, ch in enumerate(t):
        if not keep[k]:
            continue
        ch = " " if ch in " \n" else ch.translate(QUOTES)
        if ch == " " and (not out or out[-1] == " "):
            continue
        out.append(ch)
        idx.append(k)
    return "".join(out), idx


def case_ligature(sources: Path) -> bool:
    pid = "1910.03814"
    pdf = sources / "qasper_pdfs" / f"{pid}.pdf"
    qjson = [sources / "qasper-dev-v0.3.json", sources / "qasper-train-v0.3.json"]
    if not pdf.is_file() or not all(p.is_file() for p in qjson):
        print("  eval/sources/ not fetched: run `make eval-fetch`")
        return False
    qas: dict[str, Any] = {}
    for p in qjson:
        qas.update(json.loads(p.read_text()))
    paras = [norm_expected(p) for sec in qas[pid]["full_text"] for p in sec["paragraphs"]]
    results = []
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            stream = build_stream(page)
            view, idx = comparison_view(stream)
            chars = page.dedupe_chars().chars
            for pi, expected in enumerate(paras):
                j = view.find(expected) if len(expected) > 80 else -1
                if j < 0:
                    continue
                s0, s1 = idx[j], idx[j + len(expected) - 1] + 1  # slice of the page stream
                glyphs = [g for g in stream.src[s0:s1] if g is not None]
                lig = [
                    g
                    for g in dict.fromkeys(glyphs)
                    if len(unicodedata.normalize("NFKC", chars[g]["text"])) > 1
                ]
                # (1) every expanded ligature char maps to its glyph's box
                lig_ok = True
                for g in lig:
                    pos = [k for k in range(s0, s1) if stream.src[k] == g]
                    lig_ok &= len(pos) == len(unicodedata.normalize("NFKC", chars[g]["text"]))
                    lig_ok &= stream.glyph_boxes[g] is not None
                # (2) round trip: glyphs under the merged line rectangles, rebuilt from the stream
                rects = merge_line_rects(stream, glyphs, pad_pt=1.0, page=page)
                under = set()
                for g, b in enumerate(stream.glyph_boxes):
                    if b is None:
                        continue
                    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    if any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects):
                        under.add(g)
                rt = "".join(
                    ch for k, ch in enumerate(stream.text)
                    if (stream.src[k] in under) or (stream.src[k] is None and s0 <= k < s1)
                )  # fmt: skip
                sub = Stream(rt, [0] * len(rt), [], [], 0)
                rt_view, _ = comparison_view(sub)
                rt_ok = rt_view == expected and under == set(glyphs)
                results.append((pi, page.page_number, len(lig), lig_ok, rt_ok, len(rects), len(expected)))
                if lig and not any(r[2] for r in results[:-1]):  # overlay for the first ligature paragraph
                    img = render(pdf, page.page_number - 1)
                    EVIDENCE.mkdir(parents=True, exist_ok=True)
                    d = ImageDraw.Draw(img)
                    w, h = img.size
                    for r in rects:
                        d.rectangle([r[0] * w, r[1] * h, r[2] * w, r[3] * h], outline=(0, 170, 0), width=2)
                    for g in lig:
                        b = stream.glyph_boxes[g]
                        assert b is not None
                        d.rectangle([b[0] * w, b[1] * h, b[2] * w, b[3] * h], outline=(220, 0, 0), width=3)
                    u = union(rects)  # crop to the paragraph: evidence, not a copy of the paper
                    crop = (int(u[0] * w) - 12, int(u[1] * h) - 12, int(u[2] * w) + 12, int(u[3] * h) + 12)
                    img.crop(crop).save(
                        EVIDENCE / f"ligature_p{page.page_number}_para{pi}.png", optimize=True
                    )
    print(
        f"  QASPER paragraphs: {len(paras)}; "
        f"located exactly (comparison view) on a single page: {len(results)}"
    )
    for pi, pg, nl, lo, ro, nr, ln in results:
        print(
            f"  para {pi:2d} page {pg} chars={ln} ligature_glyphs={nl} "
            f"lig_map_ok={lo} roundtrip_ok={ro} rects={nr}"
        )
    with_lig = [r for r in results if r[2] > 0]
    if not with_lig:
        print("  no exactly-located paragraph contains a ligature glyph")
        return False
    first = with_lig[0]
    print(f"  ligature paragraph = QASPER para {first[0]} (page {first[1]}, {first[2]} ligature glyphs)")
    return all(r[3] and r[4] for r in results)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, choices=["cropbox", "rotate90", "rotate270", "ligature", "all"])
    ap.add_argument("--sources", type=Path, default=SOURCES, help="eval sources dir (default: eval/sources)")
    args = ap.parse_args()
    case = args.case
    runs = {
        "cropbox": case_cropbox,
        "rotate90": lambda: case_rotate(90),
        "rotate270": lambda: case_rotate(270),
        "ligature": lambda: case_ligature(args.sources),
    }
    ok = True
    for name, fn in runs.items():
        if case in (name, "all"):
            print(f"case {name}:")
            passed = fn()
            print(f"case {name}: {'PASS' if passed else 'FAIL'}")
            ok &= passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
