"""Chat-run reaper (design §8.7, F-041): finishes runs whose turn died (worker shutdown, crash) as `failed`.

A run is abandoned when it is still `running` and its session lease expired more than `GRACE_SECONDS` ago, or
when the session no longer belongs to it (another turn took over after expiry) and the run is older than
`LEASE_SECONDS + GRACE_SECONDS`. The error message goes through `persist_turn`, which locks the run row and is
idempotent, so concurrent reapers (or a late-finishing live turn) finish a run exactly once."""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.ledger import LedgerError, RunNotFound, TurnRecord, persist_turn
from app.chat.nodes._common import ERROR_TEXT, plain
from app.chat.sessions import LEASE_SECONDS
from app.core.logging import get_logger

_log = get_logger("vault.chat.reaper")

GRACE_SECONDS = 30
TURN_ABANDONED = "TURN_ABANDONED"
BATCH = 100

_FIND = text(
    "SELECT r.id, r.owner_id FROM rag_runs r "
    "LEFT JOIN chat_sessions s ON s.id = r.session_id AND s.owner_id = r.owner_id "
    "WHERE r.status = 'running' AND r.assistant_message_id IS NULL AND ("
    "(s.active_run_id = r.id AND s.run_lease_expires_at < now() - make_interval(secs => :grace)) "
    "OR (s.active_run_id IS DISTINCT FROM r.id "
    "AND r.created_at < now() - make_interval(secs => :age))) "
    "ORDER BY r.created_at LIMIT :n"
)


@dataclass(frozen=True)
class _Abandoned:
    run_id: uuid.UUID
    owner_id: uuid.UUID


async def reap_abandoned_runs(sm: async_sessionmaker[AsyncSession]) -> int:
    """Finish abandoned runs as `failed`; returns how many this call finished."""
    async with sm() as s:
        rows = (
            await s.execute(_FIND, {"grace": GRACE_SECONDS, "age": LEASE_SECONDS + GRACE_SECONDS, "n": BATCH})
        ).all()
    found = [_Abandoned(r.id, r.owner_id) for r in rows]
    reaped = 0
    for a in found:
        rec = TurnRecord(
            run_id=a.run_id,
            route="error",
            mode="auto",
            status="failed",
            scope_version_ids=(),
            generation=plain("error", ERROR_TEXT, (TURN_ABANDONED,)),
            config={"prompts": [], "models": {}, "retrieval": "n/a"},
            reason_codes=(TURN_ABANDONED,),
        )
        try:
            async with sm() as s:
                if (await persist_turn(s, a.owner_id, rec)).created:
                    reaped += 1
        except (LedgerError, RunNotFound) as e:
            _log.warning("reap_failed", error_type=type(e).__name__)
    if reaped:
        _log.info("chat_runs_reaped", count=reaped)
    return reaped
