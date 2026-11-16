"""Migration 0004: answer_segments.verification / verification_reason."""

import asyncio

import asyncpg
from alembic import command
from sqlalchemy.engine import make_url

from tests.integration.conftest import alembic_config

COLS = {"verification", "verification_reason"}


async def _columns(url: str) -> set[str]:
    conn = await asyncpg.connect(
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'answer_segments'"
        )
        return {r["column_name"] for r in rows}
    finally:
        await conn.close()


async def _check_rejects_unknown(url: str) -> bool:
    conn = await asyncpg.connect(
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        row = await conn.fetchrow(
            "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conname = 'ck_answer_segments_verification'"
        )
        return row is not None and "unsupported" in row["d"]
    finally:
        await conn.close()


def test_up_down(db_url: str) -> None:
    cfg = alembic_config(db_url)
    assert asyncio.run(_columns(db_url)) >= COLS
    assert asyncio.run(_check_rejects_unknown(db_url))
    command.downgrade(cfg, "0003")
    assert not COLS & asyncio.run(_columns(db_url))
    command.upgrade(cfg, "head")
    assert asyncio.run(_columns(db_url)) >= COLS
