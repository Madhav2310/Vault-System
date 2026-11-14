"""Incremental reader for the strict answer JSON.

Feed it text fragments as they stream in; it yields each element of `"segments": [ ... ]` the moment its
object closes, so segments (and their citations) can be validated and shown before the whole answer has
arrived. It also recovers the complete segments from a truncated document (`max_tokens`). The scanner is
string/escape aware, so braces inside segment text never confuse it.
"""

import json
import re
from typing import Any

_SEGMENTS_OPEN = re.compile(r'"segments"\s*:\s*\[')
_STATUS = re.compile(r'"status"\s*:\s*"(answer|abstain)"')


class SegmentReader:
    def __init__(self) -> None:
        self.buf = ""
        self._pos = 0
        self._depth = 0
        self._in_str = False
        self._esc = False
        self._start: int | None = None
        self._open = False
        self.closed = False  # the segments array itself has ended

    @property
    def status(self) -> str | None:
        m = _STATUS.search(self.buf)
        return m.group(1) if m else None

    def feed(self, fragment: str) -> list[dict[str, Any]]:
        """Complete segment objects that this fragment finished (possibly none, possibly several)."""
        self.buf += fragment
        out: list[dict[str, Any]] = []
        if not self._open:
            m = _SEGMENTS_OPEN.search(self.buf)
            if not m:
                return out
            self._open, self._pos = True, m.end()
        buf = self.buf
        while self._pos < len(buf) and not self.closed:
            ch = buf[self._pos]
            if self._in_str:
                if self._esc:
                    self._esc = False
                elif ch == "\\":
                    self._esc = True
                elif ch == '"':
                    self._in_str = False
            elif ch == '"':
                self._in_str = True
            elif ch == "{":
                if self._depth == 0:
                    self._start = self._pos
                self._depth += 1
            elif ch == "}":
                self._depth -= 1
                if self._depth == 0 and self._start is not None:
                    seg = json.loads(buf[self._start : self._pos + 1])
                    if isinstance(seg, dict):
                        out.append(seg)
                    self._start = None
            elif ch == "]" and self._depth == 0:
                self.closed = True
            self._pos += 1
        return out


def salvage(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """(status, complete segments) from a possibly truncated answer document."""
    reader = SegmentReader()
    segments = reader.feed(text)
    return reader.status, segments
