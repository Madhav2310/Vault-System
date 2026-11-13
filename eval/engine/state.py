"""Run state on disk: `eval/runs/<run_id>/{_state.json, results.jsonl, rounds/<NN>/change.md}`.

Every whole-file write is atomic (temp file in the same directory + `os.replace`). `results.jsonl` is
append-only, one JSON object per line, fsynced per record; a kill can leave a torn final line, which readers
ignore and `repair_results` truncates before the next append (so a resumed run never glues a record onto a
partial one). When a unit appears more than once the last line wins (a retried errored unit supersedes).
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "eval" / "runs"
STATE_SCHEMA = 1

__all__ = ["RUNS_DIR", "STATE_SCHEMA", "RunDir", "RunState", "atomic_write_text"]


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write `text` to `path` so a reader (or a crash) sees the old or the new content, never a mix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@dataclass
class RunState:
    """Contents of `_state.json`. `extra` is free-form for later tasks (noise floor, round table...) and is
    preserved verbatim across saves."""

    run_id: str
    name: str
    config_hash: str
    mode: str
    n_units: int
    started_at: str
    status: str = "running"  # running | completed | stopped | interrupted
    stopped: str | None = None  # "budget" when the ceiling was reached
    budget_usd: float | None = None
    spent_usd: float = 0.0
    completed: list[str] = field(default_factory=list)  # unit ids finished without error, in finish order
    call_ids: list[str] = field(default_factory=list)  # llm_calls ids this run paid for (spend is summed)
    batches: dict[str, dict[str, Any]] = field(default_factory=dict)  # stage -> submitted batch bookkeeping
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    schema: int = STATE_SCHEMA

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunState:
        if d.get("schema") != STATE_SCHEMA:
            raise ValueError(f"unsupported _state.json schema {d.get('schema')!r} (want {STATE_SCHEMA})")
        return cls(**d)


class RunDir:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    @property
    def state_path(self) -> pathlib.Path:
        return self.root / "_state.json"

    @property
    def results_path(self) -> pathlib.Path:
        return self.root / "results.jsonl"

    def exists(self) -> bool:
        return self.state_path.exists()

    def load_state(self) -> RunState | None:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return RunState.from_dict(json.loads(raw))

    def save_state(self, state: RunState) -> None:
        atomic_write_text(self.state_path, state.to_json())

    def repair_results(self) -> int:
        """Drop a torn final line (no trailing newline, or not valid JSON). Returns bytes removed."""
        try:
            data = self.results_path.read_bytes()
        except FileNotFoundError:
            return 0
        if not data:
            return 0
        keep = len(data)
        if not data.endswith(b"\n"):
            keep = data.rfind(b"\n") + 1
        else:
            last = data[:-1].rfind(b"\n") + 1
            try:
                json.loads(data[last:])
            except ValueError:
                keep = last
        if keep == len(data):
            return 0
        with self.results_path.open("r+b") as f:
            f.truncate(keep)
            f.flush()
            os.fsync(f.fileno())
        return len(data) - keep

    def append_result(self, record: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        with self.results_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def read_results(self) -> dict[str, dict[str, Any]]:
        """unit id -> its last complete record. A torn last line is ignored; garbage earlier raises."""
        try:
            lines = self.results_path.read_text(encoding="utf-8").split("\n")
        except FileNotFoundError:
            return {}
        out: dict[str, dict[str, Any]] = {}
        last = max((i for i, ln in enumerate(lines) if ln.strip()), default=-1)
        for i, ln in enumerate(lines):
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except ValueError:
                if i == last:
                    continue  # torn final line from a killed run
                raise ValueError(f"{self.results_path}: line {i + 1} is not valid JSON") from None
            out[rec["unit"]] = rec
        return out

    def write_change(self, round_no: int, markdown: str) -> pathlib.Path:
        """`rounds/<NN>/change.md`: what a convergence round changed and why (written by the loop)."""
        path = self.root / "rounds" / f"{round_no:02d}" / "change.md"
        atomic_write_text(path, markdown if markdown.endswith("\n") else markdown + "\n")
        return path
