"""S16.02: thread/batch/length configuration (root-cause fix, docs/plan/v3.md §6)."""

from __future__ import annotations

import os
from pathlib import Path

from app import Config, cpu_quota


def _quota(tmp: Path, text: str) -> int:
    f = tmp / "cpu.max"
    f.write_text(text)
    return cpu_quota(str(f))


def test_cpu_quota(tmp_path: Path) -> None:
    assert _quota(tmp_path, "200000 100000\n") == 2
    assert _quota(tmp_path, "50000 100000\n") == 1
    assert _quota(tmp_path, "250000 100000\n") == 3
    assert _quota(tmp_path, "max 100000\n") == min(4, os.cpu_count() or 1)
    assert cpu_quota(str(tmp_path / "missing")) == min(4, os.cpu_count() or 1)


def test_defaults(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for k in ("RERANKER_THREADS", "RERANKER_BATCH_SIZE", "RERANKER_MAX_IN_FLIGHT", "RERANKER_MAX_PASSAGE_CHARS"):
        monkeypatch.delenv(k, raising=False)
    c = Config()
    assert 1 <= c.threads <= 4 and c.batch_size == 8 and c.max_in_flight == 1 and c.max_passage_chars == 1200
