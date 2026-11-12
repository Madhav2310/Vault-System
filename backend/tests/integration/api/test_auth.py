import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.seed import seed_users


async def test_missing_key_401(db_client: httpx.AsyncClient) -> None:
    r = await db_client.get("/_test/whoami")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"
    assert r.headers["www-authenticate"] == "Bearer"


async def test_wrong_key_401(db_client: httpx.AsyncClient, session: AsyncSession) -> None:
    await seed_users(session)
    for header in ("Bearer vk_not-a-real-key", "Basic dXNlcjpwYXNz", "Bearer ", "vk_x"):
        r = await db_client.get("/_test/whoami", headers={"Authorization": header})
        assert r.status_code == 401, header
        assert r.json()["code"] == "unauthenticated"


async def test_valid_key_resolves_user(db_client: httpx.AsyncClient, session: AsyncSession) -> None:
    keys = {r.email: r.api_key for r in await seed_users(session)}
    for email, admin in (("alice@vault.local", True), ("bob@vault.local", False)):
        r = await db_client.get("/_test/whoami", headers={"Authorization": f"Bearer {keys[email]}"})
        assert r.status_code == 200
        assert r.json()["email"] == email and r.json()["is_admin"] is admin
