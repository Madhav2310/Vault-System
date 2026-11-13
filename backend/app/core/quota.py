"""Daily LLM cost quota per user (design §13.4, S18.08).

- Spend today = `sum(llm_calls.cost_usd)` since UTC midnight for the owner. The 429 check reads it through a
  30 s Redis cache; metrics read the ledger directly so they always equal the ledger sum.
- Admins are unlimited. At or over the quota: 429 `quota_exceeded` with `Retry-After` = seconds to the
  UTC midnight reset.
- Redis unavailable -> read the ledger (the cache is an optimisation, never a gate).
- Ingestion is never blocked; the brief stage calls `over_quota` and defers instead of failing.
"""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.ratelimit import get_ratelimit_redis
from app.core.settings import get_settings

CACHE_TTL_S = 30
KEY_PREFIX = "vault:quota"


def utc_midnight(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def seconds_to_reset(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    return max(1, int((utc_midnight(now) + timedelta(days=1) - now).total_seconds()) + 1)


async def spent_today(db: AsyncSession, owner_id: uuid.UUID | None, now: datetime | None = None) -> Decimal:
    """Ledger sum since UTC midnight; `owner_id=None` is every owner."""
    since = utc_midnight(now or datetime.now(UTC))
    owner = "" if owner_id is None else " AND owner_id = :o"
    params: dict[str, object] = {"s": since}
    if owner_id is not None:
        params["o"] = owner_id
    row = await db.execute(
        text("SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls WHERE created_at >= :s" + owner),  # noqa: S608
        params,
    )
    return Decimal(row.scalar_one())


async def is_admin(db: AsyncSession, owner_id: uuid.UUID) -> bool:
    row = await db.execute(text("SELECT is_admin FROM users WHERE id = :o"), {"o": owner_id})
    return bool(row.scalar_one_or_none())


async def _cached_spend(
    db: AsyncSession, redis: aioredis.Redis | None, owner_id: uuid.UUID, now: datetime
) -> Decimal:
    key = f"{KEY_PREFIX}:{owner_id}:{utc_midnight(now).date().isoformat()}"
    if redis is not None:
        try:
            hit = await redis.get(key)
            if hit is not None:
                return Decimal(hit.decode() if isinstance(hit, bytes) else str(hit))
        except (RedisError, OSError, TimeoutError):
            redis = None
    spent = await spent_today(db, owner_id, now)
    if redis is not None:
        with contextlib.suppress(RedisError, OSError, TimeoutError):
            await redis.set(key, str(spent), ex=CACHE_TTL_S)
    return spent


async def check_quota(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    admin: bool,
    redis: aioredis.Redis | None = None,
    now: datetime | None = None,
) -> None:
    """Raise 429 `quota_exceeded` when today's spend has reached the user's daily quota."""
    if admin:
        return
    now = now or datetime.now(UTC)
    limit = Decimal(str(get_settings().user_daily_usd))
    if await _cached_spend(db, redis, owner_id, now) >= limit:
        retry = seconds_to_reset(now)
        raise DomainError(
            ErrorCode.QUOTA_EXCEEDED,
            f"Daily cost quota reached; resets in {retry} s (UTC midnight)",
            headers={"Retry-After": str(retry)},
        )


async def over_quota(db: AsyncSession, owner_id: uuid.UUID) -> bool:
    """Uncached check for background work (briefs): True when the owner is a non-admin at or over quota."""
    if await is_admin(db, owner_id):
        return False
    return await spent_today(db, owner_id) >= Decimal(str(get_settings().user_daily_usd))


async def enforce_chat_quota(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[aioredis.Redis, Depends(get_ratelimit_redis)],
) -> None:
    """Route dependency for the chat POST (a turn costs money). Retries of a finished turn are blocked too."""
    await check_quota(db, user.id, admin=user.is_admin, redis=redis)
