"""Dev seeding. `python -m app.db.seed users` creates alice (admin) and bob; prints new keys once."""

import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_api_key, new_api_key
from app.db.models import User
from app.db.session import get_sessionmaker

DEV_USERS = (("alice@vault.local", True), ("bob@vault.local", False))


@dataclass(frozen=True)
class SeedResult:
    email: str
    created: bool
    api_key: str | None  # only when created; never retrievable later


async def seed_users(session: AsyncSession) -> list[SeedResult]:
    results = []
    for email, is_admin in DEV_USERS:
        existing = (await session.execute(select(User).where(User.email == email))).scalar()
        if existing is not None:
            results.append(SeedResult(email, created=False, api_key=None))
            continue
        key = new_api_key()
        session.add(User(email=email, api_key_hash=hash_api_key(key), is_admin=is_admin))
        results.append(SeedResult(email, created=True, api_key=key))
    await session.flush()
    return results


async def _main(what: str) -> None:
    if what != "users":
        raise SystemExit(f"unknown seed target {what!r}")
    async with get_sessionmaker()() as session:
        results = await seed_users(session)
        await session.commit()
    for r in results:
        # Printed once to the operator's terminal on creation (not logged).
        print(f"{r.email}: {'API key ' + r.api_key if r.api_key else 'exists (key unchanged)'}")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "users"))
