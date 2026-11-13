"""Chat endpoints (design §12): sessions (S3.01) and message posting with dedupe/turn ownership (S3.02-03)."""

import asyncio
import dataclasses
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TurnRunner, get_db, get_turn_runner
from app.api.errors import DomainError
from app.api.schemas import MessageList, PostMessageOut, SessionList, SessionOut
from app.chat import history
from app.chat import sessions as svc
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode

router = APIRouter(prefix="/v1/chat", tags=["Chat"])
_TASKS: set[asyncio.Future[None]] = set()  # strong refs: running turns must outlive the request


class CreateSession(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    title: str | None = Field(default=None, max_length=300)


class PostMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    client_message_id: str = Field(min_length=1, max_length=100)
    mode: Literal["auto", "full_context"] = "auto"


def _json(s: svc.SessionRow) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "title": s.title,
        "document_ids": [str(d) for d in s.document_ids] if s.document_ids is not None else None,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


@router.post(
    "/sessions",
    status_code=201,
    summary="Create a chat session",
    response_model=SessionOut,
    responses={404: {"description": "A document in `document_ids` is not yours or was deleted"}},
)
async def create_session(
    body: CreateSession, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    """Create a session. `document_ids` omitted/empty = all documents; foreign or deleted ids -> 404."""
    try:
        return _json(await svc.create_session(db, user.id, body.document_ids, body.title))
    except svc.ScopeDocumentNotFound:
        raise DomainError(ErrorCode.DOCUMENT_NOT_FOUND) from None


@router.get(
    "/sessions",
    summary="List chat sessions",
    description="Your sessions, most recently updated first, cursor-paginated.",
    response_model=SessionList,
)
async def list_sessions(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=svc.MAX_LIMIT)] = svc.DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    try:
        page, nxt = await svc.list_sessions(db, user.id, limit, cursor)
    except ValueError:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Invalid cursor") from None
    return {"items": [_json(s) for s in page], "next_cursor": nxt}


@router.get(
    "/sessions/{session_id}",
    summary="Get a chat session",
    description="One session with its document scope (`document_ids: null` means all documents).",
    response_model=SessionOut,
    responses={404: {"description": "Not your session"}},
)
async def get_session(
    session_id: uuid.UUID, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    try:
        return _json(await svc.get_session(db, user.id, session_id))
    except svc.SessionNotFound:
        raise DomainError(ErrorCode.NOT_FOUND) from None


@router.get(
    "/sessions/{session_id}/messages",
    summary="List messages",
    response_model=MessageList,
    response_model_exclude_unset=True,
    responses={404: {"description": "Not your session"}},
)
async def list_messages(
    session_id: uuid.UUID,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=svc.MAX_LIMIT)] = svc.DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """History, oldest first within a page; pages walk backwards from the newest (`next_cursor` = older)."""
    try:
        items, nxt = await history.list_messages(db, user.id, session_id, limit, cursor)
    except svc.SessionNotFound:
        raise DomainError(ErrorCode.NOT_FOUND) from None
    except ValueError:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Invalid cursor") from None
    return {"items": items, "next_cursor": nxt}


@router.post(
    "/sessions/{session_id}/messages",
    status_code=202,
    summary="Post a message and get a cited answer",
    response_model=PostMessageOut,
    response_model_exclude_unset=True,
    responses={
        200: {
            "model": PostMessageOut,
            "description": "Retried client_message_id of a finished turn: the stored result",
        },
        404: {"description": "Not your session"},
        409: {"description": "Another turn is running in this session (`turn_in_progress`)"},
        503: {"description": "Model provider or capacity unavailable"},
    },
)
async def post_message(
    session_id: uuid.UUID,
    body: PostMessage,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    run_turn: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> dict[str, Any]:
    """Start a turn. Retries of one `client_message_id` return the same message/run (200 when finished, 202
    while running). Another live turn in the session -> 409 `turn_in_progress`. A new turn runs to completion
    before the response (non-streaming; SSE comes with S5) and returns 200 with the assistant message; it
    keeps running and persists even if the client disconnects."""
    try:
        t = await svc.start_turn(db, user.id, session_id, body.content, body.client_message_id, body.mode)
    except svc.SessionNotFound:
        raise DomainError(ErrorCode.NOT_FOUND) from None
    except svc.TurnInProgress:
        raise DomainError(ErrorCode.TURN_IN_PROGRESS) from None
    except svc.ClientMessageIdReused:
        raise DomainError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED, "client_message_id was used with different content"
        ) from None
    if t.created:
        task: asyncio.Future[None] = asyncio.ensure_future(
            run_turn(user.id, session_id, t.run_id, t.user_message_id, body.content, body.mode)
        )
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        await asyncio.shield(task)  # a client disconnect cancels this await, not the turn
        done = await svc.start_turn(db, user.id, session_id, body.content, body.client_message_id, body.mode)
        t = dataclasses.replace(done, created=True)  # the stored result of the turn this call ran
    if t.status != "running":
        response.status_code = 200
    return {
        "run_id": str(t.run_id),
        "user_message_id": str(t.user_message_id),
        "status": t.status,
        "deduplicated": not t.created,
        "message": t.assistant_message,
    }
