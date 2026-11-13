"""S18.10 AC2: `eval.inbox` exports down-rated turns as golden-schema `draft` items."""

import datetime as dt
import pathlib
from collections.abc import Callable
from typing import Any

import httpx
import yaml
from sqlalchemy import text

import tests.support.evalkit  # noqa: F401  (puts the repo root on sys.path so `import eval` works)
from app.chat.ledger import persist_turn
from tests.support.ledger_env import make_world, record


async def test_yaml_valid(
    api: httpx.AsyncClient, sm: Any, fb_owners: Callable[[], Any], tmp_path: pathlib.Path
) -> None:
    from eval.inbox import export
    from eval.schema import GoldenItem

    o = await fb_owners()
    w = await make_world(sm, o)
    async with sm() as s:
        mid = (await persist_turn(s, o.id, record(w))).assistant_message_id
    body = {"message_id": str(mid), "rating": "down", "reason": "unsupported", "note": "claim not in doc"}
    assert (await api.post("/v1/feedback", json=body, headers=o.auth)).status_code == 200
    up = {"message_id": str(mid), "rating": "up", "segment_ordinal": 0}
    assert (await api.post("/v1/feedback", json=up, headers=o.auth)).status_code == 200

    since = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    path, n = await export(sm, since, tmp_path)
    assert path is not None and n == 1  # the up-rating is not exported

    doc = yaml.safe_load(path.read_text())
    assert len(doc["items"]) == 1
    item = GoldenItem.model_validate(doc["items"][0])
    assert item.status == "draft" and item.source == "feedback"
    assert item.question == "Q?" and item.doc_ids == [str(w.doc.document_id)]
    assert item.gold and item.gold[0].doc_id == str(w.doc.document_id)
    assert "unsupported" in (item.notes or "") and str(w.chunk_id) in (item.notes or "")

    async with sm() as s:
        q = text("SELECT eval_status FROM feedback WHERE owner_id = :o AND kind LIKE 'down%'")
        st = (await s.execute(q, {"o": o.id})).scalars().all()
    assert st == ["exported"]

    # nothing new in a window with no down-ratings
    again, m = await export(sm, dt.datetime.now(dt.UTC) + dt.timedelta(hours=1), tmp_path)
    assert again is None and m == 0
