"""`Idempotency-Key` for resource-creating POSTs (design §12, §14).

The key row is inserted in the same transaction as the work it guards. A concurrent request with the same
key blocks on the primary key until the first transaction ends: if it committed, the second request
replays the stored response; if it rolled back, the second request does the work itself. Errors are not
stored, so a failed request can be retried with the same key.
"""

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Response as Response_
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import DomainError
from app.core.error_codes import ErrorCode

MAX_KEY_LEN = 255
TTL_HOURS = 24
Response = tuple[int, dict[str, Any]]


def request_hash(**parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def check_key(key: str | None) -> str | None:
    if key is None:
        return None
    if not key or len(key) > MAX_KEY_LEN or not key.isprintable():
        raise DomainError(
            ErrorCode.VALIDATION_ERROR, f"Idempotency-Key must be 1 to {MAX_KEY_LEN} printable characters"
        )
    return key


async def run_idempotent(
    session: AsyncSession,
    owner_id: uuid.UUID,
    key: str | None,
    req_hash: str,
    op: Callable[[], Awaitable[Response]],
) -> Response:
    """Run `op` (which adds rows to `session`) at most once per (owner, key), then commit."""
    if key is None:
        resp = await op()
        await session.commit()
        return resp
    inserted = (
        await session.execute(
            text(
                "INSERT INTO idempotency_keys (owner_id, key, request_hash) VALUES (:o, :k, :h) "
                "ON CONFLICT (owner_id, key) DO NOTHING RETURNING key"
            ),
            {"o": owner_id, "k": key, "h": req_hash},
        )
    ).first()
    if inserted is None:
        row = (
            await session.execute(
                text(
                    "SELECT request_hash, response_status, response_body FROM idempotency_keys "
                    "WHERE owner_id = :o AND key = :k"
                ),
                {"o": owner_id, "k": key},
            )
        ).one()
        await session.rollback()
        if row.request_hash != req_hash:
            raise DomainError(ErrorCode.IDEMPOTENCY_KEY_REUSED)
        return int(row.response_status), dict(row.response_body)
    status, body = await op()
    await session.execute(
        text(
            "UPDATE idempotency_keys SET response_status = :s, response_body = CAST(:b AS jsonb) "
            "WHERE owner_id = :o AND key = :k"
        ),
        {"s": status, "b": json.dumps(body, default=str), "o": owner_id, "k": key},
    )
    await session.commit()
    return status, body


def canonical_response(status: int, body: dict[str, Any]) -> Response_:
    """Sorted-key JSON, so a replay from JSONB (which reorders keys) is byte-identical to the original."""
    return Response_(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str),
        status,
        media_type="application/json",
    )


async def purge_expired(session: AsyncSession, ttl_hours: int = TTL_HOURS) -> int:
    res = await session.execute(
        text("DELETE FROM idempotency_keys WHERE created_at < now() - make_interval(hours => :h)"),
        {"h": ttl_hours},
    )
    await session.commit()
    return int(res.rowcount or 0)  # type: ignore[attr-defined]
