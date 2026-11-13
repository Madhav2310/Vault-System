"""C1 checkpoint: every Vault-Eval document (and the failure fixtures) goes through upload → real extract,
quality, chunk, embed (FakeProvider vectors: no API cost) and index stages and ends in the right state.

Opt-in because it parses the full 10-K/10-Q filings (~2 min): `make c1-check` (sets VAULT_CORPUS=1).
"""

import os
import pathlib
import uuid

import pytest
import yaml
from sqlalchemy import text

from app.core.settings import get_settings
from app.ingestion.runner import run_job
from app.ingestion.stages import default_stages
from tests.support.embed import db_gateway
from tests.support.jobs import version_status
from tests.support.pipeline import upload
from tests.support.uploads import FIXTURES

ROOT = pathlib.Path(__file__).resolve().parents[4]
DOCS = yaml.safe_load((ROOT / "eval/datasets/vault_eval_v1/documents.yaml").read_text())["documents"]
pytestmark = pytest.mark.skipif(not os.environ.get("VAULT_CORPUS"), reason="opt-in: make c1-check")


async def _run_all(sm, store, enqueued, gateway, job_id):  # type: ignore[no-untyped-def]
    services = {"store": store, "settings": get_settings(), "gateway": gateway}
    out = []
    for _ in range(8):
        r = await run_job(sm, job_id, "w", default_stages(), enqueued, services=services)
        out.append(r)
        if r != "advanced":
            return out
    return out


@pytest.mark.parametrize("doc_id", sorted(DOCS))
async def test_corpus_document_reaches_ready(doc_id, api, make_owner, sm, store, enqueued, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = ROOT / DOCS[doc_id]["file"]
    if not path.exists():
        pytest.skip("eval sources not fetched (make eval-fetch)")
    o = await make_owner()
    up = await upload(api, o, path.name, path.read_bytes())
    gw, prov = db_gateway(tmp_path, sm)
    outcomes = await _run_all(sm, store, enqueued, gw, uuid.UUID(up["job_id"]))
    assert outcomes == ["advanced"] * 4 + ["finished"], (doc_id, outcomes)
    status, err = await version_status(sm, uuid.UUID(up["version_id"]))
    assert (status, err) == ("ready", None), (doc_id, status, err)
    async with sm() as s:
        n, missing, bad = (
            await s.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE embedding IS NULL), "
                    "count(*) FILTER (WHERE token_count > 800 AND kind = 'text') "
                    "FROM chunks WHERE version_id = :v"
                ),
                {"v": up["version_id"]},
            )
        ).one()
    assert n > 0 and missing == 0 and bad == 0
    print(f"{doc_id}: {n} chunks, {sum(len(c[1]) for c in prov.embed_calls)} texts embedded")


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("encrypted.pdf", ("unsupported", "encrypted")),
        ("corrupt_truncated.pdf", ("failed", "corrupt_file")),
        ("empty_text.pdf", ("needs_ocr", None)),
        ("zip_bomb.docx", ("failed", "unsafe_archive")),
    ],
)
async def test_failure_fixtures_end_correctly(
    fixture, expected, api, make_owner, sm, store, enqueued, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    up = await upload(api, o, fixture, (FIXTURES / "files" / fixture).read_bytes())
    gw, prov = db_gateway(tmp_path, sm)
    await _run_all(sm, store, enqueued, gw, uuid.UUID(up["job_id"]))
    assert await version_status(sm, uuid.UUID(up["version_id"])) == expected
    assert prov.embed_calls == []  # nothing unprocessable ever reaches the paid provider
