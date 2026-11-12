import asyncio

import asyncpg
from alembic import command
from sqlalchemy.engine import make_url

from tests.integration.conftest import alembic_config


async def _tables(url: str) -> set[str]:
    conn = await asyncpg.connect(
        make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {r["tablename"] for r in rows}
    finally:
        await conn.close()


def test_roundtrip(db_url: str) -> None:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    assert asyncio.run(_tables(db_url)) == {"alembic_version"}
    command.upgrade(cfg, "head")
    after = asyncio.run(_tables(db_url))
    assert {"users", "chunks", "chunks_p00", "chunks_p31", "llm_calls"} <= after
