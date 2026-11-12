from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.seed import seed_users


async def test_seed_users_idempotent(session: AsyncSession) -> None:
    first = await seed_users(session)
    count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    second = await seed_users(session)
    assert [r.created for r in first] == [True, True]
    assert [r.created for r in second] == [False, False] and all(r.api_key is None for r in second)
    assert (await session.execute(select(func.count()).select_from(User))).scalar_one() == count


async def test_admin_flags(session: AsyncSession) -> None:
    await seed_users(session)
    flags = {e: a for e, a in (await session.execute(select(User.email, User.is_admin))).all()}
    assert flags == {"alice@vault.local": True, "bob@vault.local": False}
