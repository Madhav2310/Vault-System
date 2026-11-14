"""Follow-up suggestions.

The answer schema carries `followups` (no extra model call). Code, not the model, enforces the limits: at most
`MAX_FOLLOWUPS`, each at most `MAX_FOLLOWUP_CHARS` characters, and none that repeats the user's question. An
abstention gets up to `MAX_FOLLOWUPS` of the in-scope documents' brief `suggested_questions` instead
(round-robin across documents, so one document does not crowd out the rest).
"""

import itertools
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.brief import EMPTY_PARAMS_HASH, KIND, PROMPT_VERSION

MAX_FOLLOWUPS = 3
MAX_FOLLOWUP_CHARS = 120


def _norm(q: str) -> str:
    return " ".join(q.casefold().split()).rstrip("?.! ")


def clean_followups(items: Iterable[object], questions: Sequence[str] = ()) -> tuple[str, ...]:
    """Trim, drop empties, over-long items, duplicates and repeats of any of `questions` (case, spacing and
    trailing punctuation do not matter); keep the first `MAX_FOLLOWUPS`."""
    seen = {_norm(q) for q in questions}
    out: list[str] = []
    for raw in items:
        if not isinstance(raw, str):
            continue
        q = " ".join(raw.split())
        if not q or len(q) > MAX_FOLLOWUP_CHARS or _norm(q) in seen:
            continue
        seen.add(_norm(q))
        out.append(q)
        if len(out) == MAX_FOLLOWUPS:
            break
    return tuple(out)


async def brief_questions(
    db: AsyncSession, owner_id: uuid.UUID, version_ids: Sequence[uuid.UUID], questions: Sequence[str] = ()
) -> tuple[str, ...]:
    """Suggested questions from the briefs of `version_ids` (owner-scoped), round-robin across documents."""
    if not version_ids:
        return ()
    rows = (
        await db.execute(
            text(
                "SELECT version_id, content -> 'suggested_questions' FROM document_insights "
                "WHERE owner_id = :o AND version_id = ANY(:v) AND kind = :k AND params_hash = :h "
                "AND prompt_version = :pv AND status = 'done' ORDER BY version_id"
            ),
            {"o": owner_id, "v": list(version_ids), "k": KIND, "h": EMPTY_PARAMS_HASH, "pv": PROMPT_VERSION},
        )
    ).all()
    await db.rollback()
    per_doc = [list(r[1] or []) for r in rows if isinstance(r[1], list)]
    mixed = [q for tier in itertools.zip_longest(*per_doc, fillvalue="") for q in tier]
    return clean_followups(mixed, questions)
