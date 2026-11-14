"""Chat sessions, turn ownership and message dedupe (design §8.7, §14). Every function takes `owner_id`."""

import base64
import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.conflicts import load_conflicts
from app.core.ids import uuid7

LEASE_SECONDS = 120
DEFAULT_LIMIT, MAX_LIMIT = 20, 100


class SessionNotFound(Exception):
    pass


class ScopeDocumentNotFound(Exception):
    """A scope document is missing, deleted, or belongs to another owner (indistinguishable on purpose)."""


class TurnInProgress(Exception):
    pass


class ClientMessageIdReused(Exception):
    """The same client_message_id arrived with different content."""


@dataclass(frozen=True)
class SessionRow:
    id: uuid.UUID
    title: str | None
    document_ids: list[uuid.UUID] | None
    created_at: dt.datetime
    updated_at: dt.datetime


def _row(r: Any) -> SessionRow:
    return SessionRow(r["id"], r["title"], r["scope_document_ids"], r["created_at"], r["updated_at"])


async def create_session(
    db: AsyncSession,
    owner_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
    title: str | None,
    *,
    commit: bool = True,
) -> SessionRow:
    """`document_ids` None or empty = whole vault (stored NULL); else all must be the owner's live docs.
    `commit=False` leaves the transaction to the caller (the idempotency wrapper commits with the key row)."""
    scope = list(dict.fromkeys(document_ids)) if document_ids else None
    if scope:
        n = (
            await db.execute(
                text(
                    "SELECT count(*) FROM documents "
                    "WHERE id = ANY(:ids) AND owner_id = :o AND deleted_at IS NULL"
                ),
                {"ids": scope, "o": owner_id},
            )
        ).scalar_one()
        if n != len(scope):
            raise ScopeDocumentNotFound
    r = (
        (
            await db.execute(
                text(
                    "INSERT INTO chat_sessions (id, owner_id, title, scope_document_ids) "
                    "VALUES (:id, :o, :t, :s) RETURNING id, title, scope_document_ids, created_at, updated_at"
                ),
                {"id": uuid7(), "o": owner_id, "t": title, "s": scope},
            )
        )
        .mappings()
        .one()
    )
    if commit:
        await db.commit()
    return _row(r)


async def get_session(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> SessionRow:
    r = (
        (
            await db.execute(
                text(
                    "SELECT id, title, scope_document_ids, created_at, updated_at FROM chat_sessions "
                    "WHERE id = :id AND owner_id = :o"
                ),
                {"id": session_id, "o": owner_id},
            )
        )
        .mappings()
        .first()
    )
    if r is None:
        raise SessionNotFound
    return _row(r)


def encode_cursor(created_at: dt.datetime, sid: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(sid)])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    """Raises ValueError on a malformed cursor."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        ts, sid = json.loads(raw)
        return dt.datetime.fromisoformat(ts), uuid.UUID(sid)
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        raise ValueError("invalid cursor") from e


async def list_sessions(
    db: AsyncSession, owner_id: uuid.UUID, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> tuple[list[SessionRow], str | None]:
    """Newest first, keyset on (created_at, id)."""
    params: dict[str, Any] = {"o": owner_id, "n": limit + 1, "c_at": None, "c_id": None}
    if cursor:
        params["c_at"], params["c_id"] = decode_cursor(cursor)
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, title, scope_document_ids, created_at, updated_at FROM chat_sessions "
                    "WHERE owner_id = :o AND (CAST(:c_at AS timestamptz) IS NULL "
                    "OR (created_at, id) < (CAST(:c_at AS timestamptz), CAST(:c_id AS uuid))) "
                    "ORDER BY created_at DESC, id DESC LIMIT :n"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    page = [_row(r) for r in rows[:limit]]
    nxt = encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return page, nxt


# --- turn ownership (§8.7) ----------------------------------------------------------------------------------
_CLAIM = text(
    "UPDATE chat_sessions SET active_run_id = :run, "
    "run_lease_expires_at = now() + make_interval(secs => :lease) "
    "WHERE id = :sid AND owner_id = :uid AND (active_run_id IS NULL OR run_lease_expires_at < now()) "
    "RETURNING id"
)


async def _claim(db: AsyncSession, session_id: uuid.UUID, owner_id: uuid.UUID, run_id: uuid.UUID) -> None:
    got = (
        await db.execute(_CLAIM, {"run": run_id, "sid": session_id, "uid": owner_id, "lease": LEASE_SECONDS})
    ).first()
    if got is None:
        raise TurnInProgress


async def claim_turn(db: AsyncSession, session_id: uuid.UUID, owner_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """CAS claim, committed. Raises TurnInProgress when a live (unexpired) turn holds the session, and also
    when the session does not exist or is not the owner's; callers resolve existence with `get_session`."""
    try:
        await _claim(db, session_id, owner_id, run_id)
    except TurnInProgress:
        await db.rollback()
        raise
    await db.commit()


async def extend_lease(
    db: AsyncSession, session_id: uuid.UUID, owner_id: uuid.UUID, run_id: uuid.UUID
) -> bool:
    """Push the lease out while the turn is still running; False if this run no longer owns the session."""
    r = await db.execute(
        text(
            "UPDATE chat_sessions SET run_lease_expires_at = now() + make_interval(secs => :lease) "
            "WHERE id = :sid AND owner_id = :uid AND active_run_id = :run RETURNING id"
        ),
        {"run": run_id, "sid": session_id, "uid": owner_id, "lease": LEASE_SECONDS},
    )
    ok = r.first() is not None
    await db.commit()
    return ok


async def release_turn(
    db: AsyncSession, session_id: uuid.UUID, owner_id: uuid.UUID, run_id: uuid.UUID
) -> bool:
    """Fenced release: clears the session only if `run_id` is still the owner. True if it released."""
    r = await db.execute(
        text(
            "UPDATE chat_sessions SET active_run_id = NULL, run_lease_expires_at = NULL "
            "WHERE id = :sid AND owner_id = :uid AND active_run_id = :run RETURNING id"
        ),
        {"run": run_id, "sid": session_id, "uid": owner_id},
    )
    ok = r.first() is not None
    await db.commit()
    return ok


# --- message dedupe (§14) -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class TurnState:
    """Outcome of posting a message. `created` is False for a retried client_message_id."""

    created: bool
    user_message_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    assistant_message: dict[str, Any] | None


async def _existing(
    db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID, client_message_id: str, content: str
) -> TurnState | None:
    m = (
        (
            await db.execute(
                text(
                    "SELECT id, content FROM chat_messages WHERE session_id = :s AND owner_id = :o "
                    "AND role = 'user' AND client_message_id = :c"
                ),
                {"s": session_id, "o": owner_id, "c": client_message_id},
            )
        )
        .mappings()
        .first()
    )
    if m is None:
        return None
    if m["content"] != content:
        raise ClientMessageIdReused
    run = (
        (
            await db.execute(
                text(
                    "SELECT id, status, assistant_message_id FROM rag_runs "
                    "WHERE user_message_id = :m AND owner_id = :o"
                ),
                {"m": m["id"], "o": owner_id},
            )
        )
        .mappings()
        .one()
    )
    answer = None
    if run["assistant_message_id"] is not None:
        a = (
            (
                await db.execute(
                    text(
                        "SELECT id, content, response_type, coverage_state, created_at, followups "
                        "FROM chat_messages "
                        "WHERE id = :a AND owner_id = :o"
                    ),
                    {"a": run["assistant_message_id"], "o": owner_id},
                )
            )
            .mappings()
            .one()
        )
        found = await load_conflicts(db, owner_id, [run["id"]])
        answer = {
            "id": str(a["id"]),
            "role": "assistant",
            "content": a["content"],
            "response_type": a["response_type"],
            "coverage_state": a["coverage_state"],
            "created_at": a["created_at"].isoformat(),
            "followups": list(a["followups"] or []),
            "conflicts": [c.model_dump() for c in found.get(run["id"], [])],
        }
    return TurnState(False, m["id"], run["id"], run["status"], answer)


async def start_turn(
    db: AsyncSession,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    content: str,
    client_message_id: str,
    mode: str = "auto",
) -> TurnState:
    """Idempotent turn start: a retried `client_message_id` returns the existing message/run (completed ->
    result, running -> run_id). Otherwise claims the session and inserts the user message and a `running` run
    in ONE transaction, so a failed insert cannot leave the session claimed.

    Raises SessionNotFound, TurnInProgress (another message's turn is live), ClientMessageIdReused."""
    await get_session(db, owner_id, session_id)
    if (prior := await _existing(db, owner_id, session_id, client_message_id, content)) is not None:
        return prior
    run_id, msg_id = uuid7(), uuid7()
    try:
        await _claim(db, session_id, owner_id, run_id)
        await db.execute(
            text(
                "INSERT INTO chat_messages (id, owner_id, session_id, role, client_message_id, content, "
                "run_id) VALUES (:id, :o, :s, 'user', :c, :t, :r)"
            ),
            {"id": msg_id, "o": owner_id, "s": session_id, "c": client_message_id, "t": content, "r": run_id},
        )
        await db.execute(
            text(
                "INSERT INTO rag_runs (id, owner_id, session_id, user_message_id, route, mode, "
                "original_query, status) VALUES (:r, :o, :s, :m, 'pending', :mode, :q, 'running')"
            ),
            {"r": run_id, "o": owner_id, "s": session_id, "m": msg_id, "mode": mode, "q": content},
        )
        await db.commit()
    except (TurnInProgress, IntegrityError):
        await db.rollback()
        # A concurrent retry of the same client_message_id won the claim or the unique key: return its turn.
        if (prior := await _existing(db, owner_id, session_id, client_message_id, content)) is not None:
            return prior
        raise
    return TurnState(True, msg_id, run_id, "running", None)
