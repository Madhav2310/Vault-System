"""Eval inbox: export down-rated answers as golden-schema `draft` items.

    cd backend && PYTHONPATH=.. uv run --frozen python -m eval.inbox --since 2026-11-01T00:00:00+00:00

Writes `eval/inbox/<date>.yaml` (same shape as `golden.yaml`: `version` + `items`) for human review; promotion
into the golden set stays manual. Each down-rating (whole answer or one segment) becomes one item:
question = the turn's original query, scope = the run's document versions' documents, `expected_answer` = the
answer that was rated (a placeholder for the reviewer to correct), gold = the sentences it cited (unlocated:
`match: not_found`, to be re-mapped by the reviewer), `notes` = reason, note and cited chunk ids. Rows move
`new -> exported`; re-running with the same window re-exports them. Reads and one status update only; no model
calls.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import pathlib
import sys
from collections.abc import Sequence
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eval.schema import GoldenItem

INBOX_DIR = pathlib.Path(__file__).resolve().parent / "inbox"
VERSION = "vault_eval_inbox"

_DOWN = (
    "SELECT f.id AS fid, f.owner_id, f.run_id, f.segment_id, f.kind, f.note, r.original_query "
    "FROM feedback f JOIN rag_runs r ON r.id = f.run_id "
    "WHERE (f.kind = 'down' OR f.kind LIKE 'down:%') AND f.created_at >= :since "
    "AND f.eval_status IN ('new', 'exported') ORDER BY f.created_at, f.id"
)
_CITED = (
    "SELECT se.chunk_id, se.cited_text, c.document_id, c.page_start "
    "FROM answer_segments a JOIN segment_evidence se ON se.segment_id = a.id "
    "JOIN chunks c ON c.id = se.chunk_id AND c.owner_id = :o "
    "WHERE a.run_id = :r AND (CAST(:seg AS uuid) IS NULL OR a.id = :seg) "
    "ORDER BY a.ordinal, se.sentence_start"
)
_ANSWER = (
    "SELECT text FROM answer_segments WHERE run_id = :r AND (CAST(:seg AS uuid) IS NULL OR id = :seg) "
    "ORDER BY ordinal"
)
_SCOPE = (
    "SELECT DISTINCT v.document_id FROM run_versions rv "
    "JOIN document_versions v ON v.id = rv.version_id WHERE rv.run_id = :r ORDER BY 1"
)


async def build_items(s: AsyncSession, since: dt.datetime) -> tuple[list[dict[str, Any]], list[Any]]:
    """Draft items (plain dicts, golden schema) and the feedback ids they came from."""
    items: list[dict[str, Any]] = []
    fids: list[Any] = []
    for f in (await s.execute(text(_DOWN), {"since": since})).mappings().all():
        p = {"r": f["run_id"], "seg": f["segment_id"]}
        answer = " ".join(t for (t,) in (await s.execute(text(_ANSWER), p)).all())
        scope = [str(d) for (d,) in (await s.execute(text(_SCOPE), {"r": f["run_id"]})).all()]
        cited = (await s.execute(text(_CITED), {**p, "o": f["owner_id"]})).mappings().all()
        reason = f["kind"].partition(":")[2] or None
        gold = [
            {
                "quote": c["cited_text"],
                "page": c["page_start"] if c["page_start"] >= 1 else None,
                "match": "not_found",
                "doc_id": str(c["document_id"]),
            }
            for c in cited
        ]
        notes = [f"feedback: down{':' + reason if reason else ''}"]
        if f["note"]:
            notes.append(f"user note: {f['note']}")
        if cited:
            notes.append("cited chunks: " + ", ".join(sorted({str(c["chunk_id"]) for c in cited})))
        item: dict[str, Any] = {
            "id": f"feedback:{str(f['run_id'])[:8]}:{str(f['fid'])[:8]}",
            "source": "feedback",
            "category": "lookup",
            "doc_ids": scope or None,
            "question": f["original_query"],
            "expected_answer": answer or "(no answer text; reviewer to write the expected answer)",
            "expected_coverage": "supported",
            "grading": "judge" if gold else "rubric",
            "gold": gold,
            "status": "draft",
            "notes": "; ".join(notes),
        }
        if not gold:
            item["rubric"] = ["Reviewer to define: the rated answer cited nothing."]
        GoldenItem.model_validate(item)  # fail loudly rather than write an invalid inbox
        items.append(item)
        fids.append(f["fid"])
    return items, fids


def _write(items: list[dict[str, Any]], out_dir: pathlib.Path) -> pathlib.Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{dt.datetime.now(dt.UTC).date().isoformat()}.yaml"
    doc = {"version": VERSION, "items": items}
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return path


async def export(
    sm: async_sessionmaker[AsyncSession], since: dt.datetime, out_dir: pathlib.Path = INBOX_DIR
) -> tuple[pathlib.Path | None, int]:
    """Write `<out_dir>/<today>.yaml`; returns (path or None when nothing to export, item count)."""
    async with sm() as s:
        items, fids = await build_items(s, since)
        if not items:
            return None, 0
        path = _write(items, out_dir)
        await s.execute(
            text("UPDATE feedback SET eval_status = 'exported' WHERE id = ANY(:ids) AND eval_status = 'new'"),
            {"ids": fids},
        )
        await s.commit()
    return path, len(items)


async def _main(a: argparse.Namespace) -> int:
    from app.db.session import get_sessionmaker

    path, n = await export(get_sessionmaker(), dt.datetime.fromisoformat(a.since))
    print(f"exported {n} item(s)" + (f" to {path}" if path else ""))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.inbox")
    p.add_argument(
        "--since", required=True, metavar="ISO", help="export down-ratings created at or after this"
    )
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
