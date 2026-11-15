"""Daily LLM cost quota per user.

- Spend today = `sum(llm_calls.cost_usd)` since UTC midnight for the owner, read from the ledger (one index
  scan on `ix_llm_calls_owner_created`).
- A chat turn's cost lands in the ledger only while it runs, so admission also counts the owner's turns in
  flight: each holds a reservation of `TURN_RESERVE_USD` in a Redis sorted set (one atomic script checks and
  adds), released when the turn ends and expiring on its own after `RESERVATION_TTL_S` if the process dies.
  A turn is admitted while `spent + in_flight * TURN_RESERVE_USD < quota`, so concurrent turns cannot run
  far past the quota: the overshoot is bounded by the turns that finish during one admission's ledger read.
- Admins are unlimited. At or over the quota: 429 `quota_exceeded` with `Retry-After` = seconds to the
  UTC midnight reset.
- Redis unavailable -> the ledger check alone (reservations are a refinement, never a gate).
- Ingestion is never blocked; the brief stage calls `over_quota` and defers instead of failing.
"""

import contextlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import DomainError
from app.core.error_codes import ErrorCode
from app.core.settings import get_settings
from app.explain.templates import QUOTA_REACHED

KEY_PREFIX = "vault:quota:inflight"
# Held per turn in flight. The most expensive turn of the 252 final evaluation turns cost $0.027
# (eval/baselines/v2_final); the mean is $0.007.
TURN_RESERVE_USD = Decimal("0.05")
RESERVATION_TTL_S = 180  # longer than the longest route deadline (60 s)

# KEYS[1] the owner's in-flight set; ARGV: now, spent, reserve, limit, expires_at, token, ttl.
# Returns -1 (over quota) or the number of turns in flight including this one.
_RESERVE = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local n = redis.call('ZCARD', KEYS[1])
if tonumber(ARGV[2]) + n * tonumber(ARGV[3]) >= tonumber(ARGV[4]) then return -1 end
redis.call('ZADD', KEYS[1], ARGV[5], ARGV[6])
redis.call('EXPIRE', KEYS[1], ARGV[7])
return n + 1
"""


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


@dataclass
class TurnReservation:
    """One admitted chat turn's hold on the quota; `release` is idempotent and never raises."""

    redis: aioredis.Redis | None = None
    key: str | None = None
    token: str | None = None
    handed_off: bool = False

    async def release(self) -> None:
        if self.redis is None or self.key is None or self.token is None:
            return
        redis, self.redis = self.redis, None
        with contextlib.suppress(RedisError, OSError, TimeoutError):
            await redis.zrem(self.key, self.token)

    def guard(self, run: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
        """`run` wrapped so the turn releases the reservation when it ends. Calling the wrapper hands the
        reservation over to the turn (the request no longer releases it)."""

        async def guarded(*args: object) -> None:
            try:
                await run(*args)
            finally:
                await self.release()

        def start(*args: object) -> Awaitable[None]:
            self.handed_off = True
            return guarded(*args)

        return start


def _quota_exceeded(now: datetime) -> DomainError:
    retry = seconds_to_reset(now)
    return DomainError(
        ErrorCode.QUOTA_EXCEEDED, QUOTA_REACHED.format(retry=retry), headers={"Retry-After": str(retry)}
    )


async def check_quota(
    db: AsyncSession,
    owner_id: uuid.UUID,
    *,
    admin: bool,
    redis: aioredis.Redis | None = None,
    now: datetime | None = None,
) -> TurnReservation:
    """Admit one chat turn or raise 429 `quota_exceeded`; the returned reservation must be released when the
    turn ends (or at once when no turn starts)."""
    if admin:
        return TurnReservation()
    now = now or datetime.now(UTC)
    limit = Decimal(str(get_settings().user_daily_usd))
    spent = await spent_today(db, owner_id, now)
    if spent >= limit:
        raise _quota_exceeded(now)
    if redis is None:
        return TurnReservation()
    key = f"{KEY_PREFIX}:{owner_id}"
    token = uuid.uuid4().hex
    ts = now.timestamp()
    try:
        n = await redis.eval(
            _RESERVE, 1, key, str(ts), str(spent), str(TURN_RESERVE_USD), str(limit),
            str(ts + RESERVATION_TTL_S), token, str(RESERVATION_TTL_S),
        )  # fmt: skip
    except (RedisError, OSError, TimeoutError):
        return TurnReservation()
    if int(n) < 0:
        raise _quota_exceeded(now)
    return TurnReservation(redis, key, token)


async def over_quota(db: AsyncSession, owner_id: uuid.UUID) -> bool:
    """Uncached check for background work (briefs): True when the owner is a non-admin at or over quota."""
    if await is_admin(db, owner_id):
        return False
    return await spent_today(db, owner_id) >= Decimal(str(get_settings().user_daily_usd))
