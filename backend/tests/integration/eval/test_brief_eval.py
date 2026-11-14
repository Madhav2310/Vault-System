"""S12.07 AC1: the report of a dry run over in-memory documents has per-document rows and the three totals."""

import json
import pathlib
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from eval import brief_eval as be
from eval.judge import SpendGuard
from eval.loader import load_dataset

from app.chat.evidence import EvidenceChunk
from app.llm.gateway import MemoryLedger
from tests.support.brief_kit import chunk

TEXT = (
    "This Consulting Agreement is made on the 1st day of March 2020 between Medalist Diversified REIT, Inc. "
    "and Acme Consulting LLC. This Agreement is governed by the laws of the State of Delaware. "
    "The Consultant shall deliver monthly reports."
)
KEYS = ["auth_amendment", "cuad_consulting", "cuad_endorsement"]


def source(key: str, meta: Mapping[str, Any]) -> list[EvidenceChunk]:
    vid = uuid.uuid4()
    c = chunk(TEXT, title=key)
    return [
        EvidenceChunk(c.chunk_id, vid, c.doc_title, 1, 1, 1, c.section_path, c.original_text, c.sentences)
    ]


async def run(cap: str = "1000", facts_per_doc: int = 10) -> be.BriefEvalResult:
    ledger = MemoryLedger()
    gw = be._dry_gateway(ledger)
    return await be.run_brief_eval(
        KEYS, load_dataset(), source, gw, SpendGuard(ledger, Decimal(cap)), mode="dry-run",
        facts_per_doc=facts_per_doc, git_sha="test",
    )  # fmt: skip


async def test_report_shape(tmp_path: pathlib.Path) -> None:
    r = await run()
    t = r.totals()
    assert t["documents"] == 3 and t["facts"] == 9 and t["judged"] == 9
    assert t["faithfulness"] == 1.0 and t["verified_rate"] is not None and t["completeness"] is not None
    path = be.write_report(r, tmp_path)
    assert path.name == f"brief_{r.run_id}.md"
    md = path.read_text()
    for needle in (
        "## Totals",
        "faithfulness (mean verdict score)",
        "completeness",
        "verified rate",
        "DRY RUN",
    ):
        assert needle in md
    rows = [ln for ln in md.splitlines() if ln.startswith("| ") and ln.split("|")[1].strip() in KEYS]
    assert [ln.split("|")[1].strip() for ln in rows] == KEYS  # one row per document, in order
    for ln in rows:
        assert (
            len(ln.split("|")) == 14
        )  # document, category, facts, verified, rate, judged, faith, comp, cost, brief cost, latency, error
    head = next(ln for ln in md.splitlines() if ln.startswith("| document |"))
    assert "verified rate" in head and "faithfulness" in head and "completeness" in head
    base = be.freeze_baseline(r, tmp_path)
    data = json.loads(base.read_text())
    assert set(data["metrics"]) == {"faithfulness", "faithfulness_pass_rate", "completeness", "verified_rate"}
    assert set(data["per_doc"]) == set(KEYS) and data["run_id"] == str(r.run_id)


async def test_facts_per_doc_samples_and_cap_stops_before_the_next_call(tmp_path: pathlib.Path) -> None:
    r = await run(facts_per_doc=2)
    assert all(d.judged == 2 for d in r.docs) and r.totals()["facts"] == 9
    stopped = await run(cap="0.0001")  # below the worst case of the first call: nothing is sent
    assert stopped.stopped and stopped.docs == [] and stopped.spent == 0
    assert "STOPPED" in be.render_report(stopped)
    mid = await run(cap="0.05")  # enough for some calls, not all
    assert mid.stopped is not None and mid.spent <= Decimal("0.05")
