import uuid

from sqlalchemy import text

from app.worker.tasks.maintenance import purge_idempotency_keys


async def test_idempotency_purge(sm, make_owner) -> None:  # type: ignore[no-untyped-def]
    o = await make_owner()
    async with sm() as s:
        for key, age_h in (("old", 25), ("edge", 23), ("new", 0)):
            await s.execute(
                text(
                    "INSERT INTO idempotency_keys (owner_id, key, request_hash, created_at) "
                    "VALUES (:o, :k, 'h', now() - make_interval(hours => :a))"
                ),
                {"o": o.id, "k": key, "a": age_h},
            )
        await s.commit()
    deleted = await purge_idempotency_keys(sm)
    async with sm() as s:
        left = (
            (
                await s.execute(
                    text("SELECT key FROM idempotency_keys WHERE owner_id = :o ORDER BY key"), {"o": o.id}
                )
            )
            .scalars()
            .all()
        )
    assert left == ["edge", "new"] and deleted >= 1
    assert uuid.UUID(str(o.id))
