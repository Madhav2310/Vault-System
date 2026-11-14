"""Answer feedback: one rating per (answer message, segment or whole answer) per user.

Storage is the existing `feedback` table (run_id, segment_id, chunk_id, kind, note): `kind` is `up`, `down` or
`down:<reason>`. Its unique key includes `kind`, so a changed rating would be a second row; this module keeps
"last write wins" by updating the user's single row for that target under an advisory lock instead.

The note is user text: it is stored and returned, never logged.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7

UP, DOWN = "up", "down"
REASONS = ("wrong", "unsupported", "incomplete", "other")


class MessageNotFound(Exception):
    """Not the caller's, not an assistant answer, or has no run."""


class SegmentOutOfRange(Exception):
    pass


@dataclass(frozen=True)
class FeedbackRow:
    id: uuid.UUID
    message_id: uuid.UUID
    rating: str
    reason: str | None
    segment_ordinal: int | None
    note: str | None


def encode_kind(rating: str, reason: str | None) -> str:
    return f"{rating}:{reason}" if rating == DOWN and reason else rating


def decode_kind(kind: str) -> tuple[str, str | None]:
    rating, _, reason = kind.partition(":")
    return rating, reason or None


async def record_feedback(
    db: AsyncSession,
    owner_id: uuid.UUID,
    message_id: uuid.UUID,
    rating: str,
    reason: str | None,
    segment_ordinal: int | None,
    note: str | None,
) -> FeedbackRow:
    """Insert or replace the caller's rating of an answer (or of one of its segments), then commit."""
    msg = (
        await db.execute(
            text(
                "SELECT run_id FROM chat_messages WHERE id = :m AND owner_id = :o "
                "AND role = 'assistant' AND run_id IS NOT NULL"
            ),
            {"m": message_id, "o": owner_id},
        )
    ).first()
    if msg is None:
        raise MessageNotFound
    run_id: uuid.UUID = msg.run_id
    segment_id: uuid.UUID | None = None
    if segment_ordinal is not None:
        segment_id = (
            await db.execute(
                text("SELECT id FROM answer_segments WHERE run_id = :r AND ordinal = :n"),
                {"r": run_id, "n": segment_ordinal},
            )
        ).scalar()
        if segment_id is None:
            raise SegmentOutOfRange
    kind = encode_kind(rating, reason)
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"feedback:{owner_id}:{run_id}:{segment_id}"},
    )
    existing = (
        await db.execute(
            text(
                "SELECT id FROM feedback WHERE owner_id = :o AND run_id = :r "
                "AND segment_id IS NOT DISTINCT FROM :s AND chunk_id IS NULL ORDER BY created_at LIMIT 1"
            ),
            {"o": owner_id, "r": run_id, "s": segment_id},
        )
    ).scalar()
    if existing is None:
        fid = uuid7()
        await db.execute(
            text(
                "INSERT INTO feedback (id, owner_id, run_id, segment_id, kind, note) "
                "VALUES (:id, :o, :r, :s, :k, :n)"
            ),
            {"id": fid, "o": owner_id, "r": run_id, "s": segment_id, "k": kind, "n": note},
        )
    else:
        fid = existing
        # A changed verdict is new input for the eval inbox again.
        await db.execute(
            text(
                "UPDATE feedback SET kind = :k, note = :n, "
                "eval_status = CASE WHEN kind = :k AND note IS NOT DISTINCT FROM :n "
                "THEN eval_status ELSE 'new' END WHERE id = :id"
            ),
            {"k": kind, "n": note, "id": fid},
        )
    await db.commit()
    return FeedbackRow(fid, message_id, rating, reason if rating == DOWN else None, segment_ordinal, note)


async def feedback_summary(db: AsyncSession, owner_id: uuid.UUID | None, window_s: int) -> dict[str, object]:
    """`{up, down, by_reason}` over feedback created within the window; `owner_id=None` is global scope.

    This is the `feedback` object `/v1/metrics/chat` adds."""
    where = "created_at >= now() - make_interval(secs => :w)" + (" AND owner_id = :o" if owner_id else "")
    params: dict[str, object] = {"w": window_s}
    if owner_id:
        params["o"] = owner_id
    rows = (
        await db.execute(
            text(f"SELECT kind, count(*) FROM feedback WHERE {where} GROUP BY kind"),  # noqa: S608
            params,
        )
    ).all()
    out: dict[str, object] = {"up": 0, "down": 0}
    by_reason: dict[str, int] = {}
    for kind, n in rows:
        rating, reason = decode_kind(kind)
        out[rating] = int(out.get(rating, 0)) + int(n)  # type: ignore[call-overload]
        if rating == DOWN and reason:
            by_reason[reason] = by_reason.get(reason, 0) + int(n)
    out["by_reason"] = dict(sorted(by_reason.items()))
    return out
