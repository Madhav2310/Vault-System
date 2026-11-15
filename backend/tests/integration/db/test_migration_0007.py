"""Migration 0007: a job left unfinished at the removed `contextualize` stage is finished, others are not."""

import asyncio
import uuid

from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User
from tests.integration.conftest import alembic_config
from tests.support.docs import seed_document

SM = async_sessionmaker[AsyncSession]
CASES = [  # (current_stage, status before) -> status after
    (("contextualize", "pending"), "succeeded"),
    (("contextualize", "running"), "succeeded"),
    (("contextualize", "failed"), "succeeded"),
    (("contextualize", "dead"), "dead"),
    (("brief", "pending"), "pending"),
]


async def test_unfinished_contextualize_jobs_are_finished(db_url: str, sm: SM) -> None:
    uid = uuid.uuid4()
    async with sm() as s:
        s.add(User(id=uid, email=f"{uid}@test.local", api_key_hash=f"h-{uid}"))
        await s.commit()
    versions = [(await seed_document(sm, uid)).version_id for _ in CASES]
    cfg = alembic_config(db_url)
    await asyncio.to_thread(command.downgrade, cfg, "0006")
    async with sm() as s:
        for v, ((stage, status), _) in zip(versions, CASES, strict=True):
            await s.execute(
                text(
                    "UPDATE processing_jobs SET current_stage = :c, status = :st, lease_owner = 'w', "
                    "error_code = 'x' WHERE version_id = :v"
                ),
                {"c": stage, "st": status, "v": v},
            )
        await s.commit()
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with sm() as s:
        rows = {
            r.version_id: r
            for r in await s.execute(
                text(
                    "SELECT version_id, status, lease_owner, error_code FROM processing_jobs "
                    "WHERE version_id = ANY(:v)"
                ),
                {"v": versions},
            )
        }
    for v, (_, after) in zip(versions, CASES, strict=True):
        assert rows[v].status == after
        assert (rows[v].lease_owner, rows[v].error_code) == (
            (None, None) if after == "succeeded" else ("w", "x")
        )
