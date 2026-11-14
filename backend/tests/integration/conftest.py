"""Integration fixtures: a dedicated `vault_test_<checkout hash>` database migrated to head once per session.

Needs the compose Postgres (`make up`, or `docker compose up -d postgres`). Never touches the dev DB.
"""

import asyncio
import hashlib
import os
import pathlib
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_db, get_enqueue, get_store, get_turn_runner
from app.core.auth import hash_api_key, new_api_key
from app.db.models import User
from app.main import create_app
from app.storage.local import LocalObjectStore

BACKEND = pathlib.Path(__file__).resolve().parents[2]
# One test database per checkout (F-014): parallel worktrees share the compose Postgres, and a shared name let
# one run drop the database under another. Override with VAULT_TEST_DB.
TEST_DB = (
    os.environ.get("VAULT_TEST_DB") or "vault_test_" + hashlib.sha1(str(BACKEND).encode()).hexdigest()[:8]
)


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


@pytest.fixture(autouse=True)
def _scripted_turns_use_fusion_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scripted fake providers list every LLM call of a turn in order. The default retrieval config reranks
    with one extra LLM call, so these tests of turn mechanics run on `retrieval@2` (fusion order);
    `chat/test_default_retrieval_turn.py` covers the default with a scripted rerank step."""
    from app.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "retrieval_config", "retrieval@2")


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


# --- app-level fixtures: the real app over the test DB (real commits), temp object store, fresh owners ---
@dataclass
class Owner:
    id: uuid.UUID
    key: str

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}"}


@pytest.fixture
def sm(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def store(tmp_path: pathlib.Path) -> LocalObjectStore:
    return LocalObjectStore(tmp_path / "blobs")


@pytest.fixture
async def make_owner(sm: async_sessionmaker[AsyncSession]) -> AsyncIterator[Callable[[], object]]:
    created: list[uuid.UUID] = []

    async def make() -> Owner:
        uid, key = uuid.uuid4(), new_api_key()
        async with sm() as s:
            s.add(User(id=uid, email=f"{uid}@test.local", api_key_hash=hash_api_key(key)))
            await s.commit()
        created.append(uid)
        return Owner(uid, key)

    yield make
    async with sm() as s:
        p = {"ids": created}
        shas = (
            (
                await s.execute(
                    text("SELECT DISTINCT sha256 FROM document_versions WHERE owner_id = ANY(:ids)"), p
                )
            )
            .scalars()
            .all()
        )
        for stmt in (
            "DELETE FROM job_events WHERE job_id IN "
            "(SELECT id FROM processing_jobs WHERE owner_id = ANY(:ids))",
            "DELETE FROM llm_calls WHERE owner_id = ANY(:ids)",
            "DELETE FROM processing_jobs WHERE owner_id = ANY(:ids)",
            "DELETE FROM pages WHERE version_id IN "
            "(SELECT id FROM document_versions WHERE owner_id = ANY(:ids))",
            "DELETE FROM chunks WHERE owner_id = ANY(:ids)",
            "DELETE FROM document_insights WHERE owner_id = ANY(:ids)",
            "UPDATE documents SET current_version_id = NULL WHERE owner_id = ANY(:ids)",
            "DELETE FROM document_versions WHERE owner_id = ANY(:ids)",
            "DELETE FROM documents WHERE owner_id = ANY(:ids)",
            "DELETE FROM idempotency_keys WHERE owner_id = ANY(:ids)",
            "DELETE FROM users WHERE id = ANY(:ids)",
        ):
            await s.execute(text(stmt), p)
        await s.execute(
            text(
                "DELETE FROM blobs b WHERE sha256 = ANY(:shas) "
                "AND NOT EXISTS (SELECT 1 FROM document_versions v WHERE v.sha256 = b.sha256)"
            ),
            {"shas": list(shas)},
        )
        await s.commit()


class EnqueueRecorder:
    """Stands in for Celery: records (stage, job_id) instead of sending to Redis."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, uuid.UUID]] = []

    async def __call__(self, stage: str, job_id: uuid.UUID) -> None:
        self.calls.append((stage, job_id))


@pytest.fixture
def enqueued() -> EnqueueRecorder:
    return EnqueueRecorder()


@pytest.fixture
async def api(
    sm: async_sessionmaker[AsyncSession], store: LocalObjectStore, enqueued: EnqueueRecorder
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sm() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_enqueue] = lambda: enqueued

    async def _no_turn(*_a: object) -> None:  # chat tests that need a finished turn override this
        return None

    app.dependency_overrides[get_turn_runner] = lambda: _no_turn
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        yield c
