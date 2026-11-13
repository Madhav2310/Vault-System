"""Glyph geometry and the glyph-mapped character stream (research 05 §3.1 and §3.3; S0.21 spike).

- `PageGeo` converts pdfplumber coordinates (MediaBox-relative, /Rotate applied) to boxes normalized to the
  rendered page (CropBox offset removed, rotation applied, top-left origin, [0, 1]).
- `upright_chars` re-expresses the page's glyphs in the frame where the text is upright, so word extraction
  reads rotated pages in natural order (F6).
- `Line` / `build_lines` turn words into reading-order lines that keep, per character, the glyph it came from.
  Ligatures and multi-character glyphs expand with NFKC; every expanded char maps to the same glyph (F7, F8).
"""

import math
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pdfplumber.utils.text import WordExtractor

from app.ingestion.extract.base import Box

EXTRACT_CFG: dict[str, Any] = {
    "use_text_flow": True,
    "x_tolerance_ratio": 0.15,
}  # extraction sweep, design §6.4
BOLD_MARKERS = ("bold", "black", "heavy", "semibold", "demi")


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
    """Inverse of rotate_box; (w, h) are the upright frame's dimensions."""
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


@dataclass(frozen=True)
class PageGeo:
    media: tuple[float, float, float, float]  # PDF user space, bottom-left origin
    crop: tuple[float, float, float, float]
    rotation: int

    @classmethod
    def of(cls, page: Any) -> "PageGeo":
        m, c = page.page_obj.mediabox, page.page_obj.cropbox
        return cls(
            (float(m[0]), float(m[1]), float(m[2]), float(m[3])),
            (float(c[0]), float(c[1]), float(c[2]), float(c[3])),
            int(page.rotation or 0) % 360,
        )

    @property
    def media_wh(self) -> tuple[float, float]:
        return self.media[2] - self.media[0], self.media[3] - self.media[1]

    @property
    def crop_wh(self) -> tuple[float, float]:
        return self.crop[2] - self.crop[0], self.crop[3] - self.crop[1]

    def display_wh(self) -> tuple[float, float]:
        w, h = self.crop_wh
        return (h, w) if self.rotation in (90, 270) else (w, h)

    def normalize(self, plumber_box: Box) -> Box:
        """pdfplumber box -> normalized rendered-page box (F5, F6, F12); clamped, zero-size when outside."""
        b = unrotate_box(plumber_box, self.rotation, *self.media_wh)
        dx = self.crop[0] - self.media[0]
        dy = self.media[3] - self.crop[3]  # CropBox top, measured from the MediaBox top
        x0, t, x1, bt = b[0] - dx, b[1] - dy, b[2] - dx, b[3] - dy
        cw, ch = self.crop_wh
        x0, t, x1, bt = rotate_box((x0, t, x1, bt), self.rotation, cw, ch)
        dw, dh = self.display_wh()

        def c(v: float) -> float:
            return round(min(1.0, max(0.0, v)), 4)

        return c(x0 / dw), c(t / dh), c(x1 / dw), c(bt / dh)


def text_rotation(chars: Sequence[dict[str, Any]]) -> int:
    """Dominant clockwise rotation (0/90/180/270) of glyph baselines in pdfplumber's display space."""
    votes: dict[int, int] = {}
    for ch in chars:
        if not ch["text"].strip():
            continue
        a, b = ch["matrix"][0], ch["matrix"][1]
        ang = round(math.degrees(math.atan2(-b, a)) / 90) * 90 % 360
        votes[ang] = votes.get(ang, 0) + 1
    return max(votes, key=lambda k: votes[k]) if votes else 0


def upright_chars(chars: Sequence[dict[str, Any]], width: float, height: float) -> list[dict[str, Any]]:
    """Copies of the chars in the frame where the dominant text direction is upright; `_g` = glyph index."""
    rot = text_rotation(chars)
    uw, uh = (height, width) if rot in (90, 270) else (width, height)
    out = []
    for i, ch in enumerate(chars):
        x0, t, x1, b = unrotate_box((ch["x0"], ch["top"], ch["x1"], ch["bottom"]), rot, uw, uh)
        out.append({**ch, "x0": x0, "x1": x1, "top": t, "bottom": b, "doctop": t, "upright": True, "_g": i})
    return out


@dataclass
class Line:
    """One reading-order line: its characters with their glyph sources (SYNTH spaces between words)."""

    text: str
    src: list[int]  # glyph index per char, -1 for synthetic spaces
    x0: float
    x1: float
    top: float
    bottom: float
    size: float  # median font size of its glyphs
    bold: bool
    words: list[dict[str, Any]] = field(default_factory=list)

    @property
    def height(self) -> float:
        return self.bottom - self.top


def _expand(word: dict[str, Any]) -> tuple[str, list[int]]:
    text: list[str] = []
    src: list[int] = []
    for ch in word["chars"]:
        exp = unicodedata.normalize("NFKC", ch["text"])  # ligatures / multi-char glyphs (F7, F8)
        text.extend(exp)
        src.extend([ch["_g"]] * len(exp))
    return "".join(text), src


def extract_words(chars: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = WordExtractor(**EXTRACT_CFG).extract_words(list(chars), return_chars=True)
    return words


def build_lines(words: Sequence[dict[str, Any]]) -> list[Line]:
    """Group consecutive words (content-stream order) into lines: a new line starts when the vertical
    position changes by more than half the smaller word height."""
    lines: list[Line] = []
    cur: list[dict[str, Any]] = []

    def flush() -> None:
        if not cur:
            return
        text: list[str] = []
        src: list[int] = []
        for i, w in enumerate(cur):
            if i:
                text.append(" ")
                src.append(-1)
            t, s = _expand(w)
            text.append(t)
            src.extend(s)
        sizes = [float(c.get("size", 0.0)) for w in cur for c in w["chars"]]
        fonts = [str(c.get("fontname", "")).lower() for w in cur for c in w["chars"]]
        bold = sum(any(m in f for m in BOLD_MARKERS) for f in fonts) > len(fonts) / 2
        lines.append(
            Line(
                "".join(text),
                src,
                min(w["x0"] for w in cur),
                max(w["x1"] for w in cur),
                min(w["top"] for w in cur),
                max(w["bottom"] for w in cur),
                statistics.median(sizes) if sizes else 0.0,
                bold,
                list(cur),
            )
        )
        cur.clear()

    for w in words:
        if cur:
            p = cur[-1]
            if abs(w["top"] - p["top"]) > 0.5 * min(w["bottom"] - w["top"], p["bottom"] - p["top"]):
                flush()
        cur.append(w)
    flush()
    return lines
