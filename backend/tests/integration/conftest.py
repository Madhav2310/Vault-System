"""Integration fixtures: a dedicated `vault_test` database migrated to head once per session.

Needs the compose Postgres (`make up`, or `docker compose up -d postgres`). Never touches the dev DB.
"""

import asyncio
import os
import pathlib
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

BACKEND = pathlib.Path(__file__).resolve().parents[2]
TEST_DB = "vault_test"


def _urls() -> tuple[str, str]:
    base = make_url(os.environ["DATABASE_URL"])
    test = base.set(database=TEST_DB).render_as_string(hide_password=False)
    admin = base.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    return test, admin


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "app/db/migrations"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"url={url}"]})()  # type: ignore[assignment]
    return cfg


async def _recreate(admin_url: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    test_url, admin_url = _urls()
    asyncio.run(_recreate(admin_url))
    command.upgrade(alembic_config(test_url), "head")
    yield test_url


@pytest.fixture
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(db_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back after the test."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        async with async_sessionmaker(bind=conn, expire_on_commit=False)() as s:
            yield s
        await trans.rollback()
