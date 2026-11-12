from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import hash_api_key
from app.db.seed import seed_users


async def test_only_hash_persisted(session: AsyncSession) -> None:
    results = await seed_users(session)
    raw_keys = [r.api_key for r in results if r.api_key]
    assert len(raw_keys) == 2
    dump = (await session.execute(text("SELECT row_to_json(u)::text FROM users u"))).scalars().all()
    blob = "\n".join(dump)
    for raw in raw_keys:
        assert raw not in blob
        assert hash_api_key(raw) in blob
