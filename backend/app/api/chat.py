"""Chat endpoints: sessions and message posting with dedupe/turn ownership."""

import asyncio
import dataclasses
import uuid
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TurnRunner, get_db, get_turn_runner
from app.api.errors import DomainError
from app.api.idempotency import canonical_response, check_key, request_hash, run_idempotent
from app.api.schemas import EventSchemaOut, MessageList, PostMessageOut, SessionList, SessionOut
from app.chat import history
from app.chat import sessions as svc
from app.chat import stream as stream_mod
from app.chat.events import event_schema
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.quota import enforce_chat_quota

router = APIRouter(prefix="/v1/chat", tags=["Chat"])
_TASKS: set[asyncio.Future[None]] = set()  # strong refs: running turns must outlive the request


class CreateSession(BaseModel):
    document_ids: list[uuid.UUID] | None = None
    title: str | None = Field(default=None, max_length=300)


class ClarifyChoice(BaseModel):
    message_id: uuid.UUID = Field(description="Id of the `clarify` assistant message the option belongs to.")
    option: int = Field(
        ge=0, le=3, description="Index of the chosen option in that message's `clarify_options`."
    )


class PostMessage(BaseModel):
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=20000,
        description="The question. Omit it when `reask_of` is set.",
    )
    client_message_id: str = Field(min_length=1, max_length=100)
    mode: Literal["auto", "full_context"] = "auto"
    reask_of: uuid.UUID | None = Field(
        default=None,
        description="Id of an earlier message of this session (a stale answer or its question): its question "
        "is asked again as a new turn on the current versions of the documents.",
    )
    replaces: uuid.UUID | None = Field(
        default=None,
        description="Id of a message of this session whose turn this one replaces (Regenerate, Edit and "
        "resend): once this turn completes, the replaced question and answer leave the history.",
    )
    clarify_choice: ClarifyChoice | None = Field(
        default=None,
        description="The option picked from a clarification: the clarified question is asked again, with the "
        "scope narrowed by the server to the chosen document. Omit `content` and `reask_of`.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"content": "What is the notice period?", "client_message_id": "c-0001", "mode": "auto"},
                {"reask_of": "0193c2a4-7b1e-7c3a-9d55-2f6e8a1b4c90", "client_message_id": "c-0002"},
            ]
        }
    )

    @model_validator(mode="after")
    def _content_or_reask(self) -> Self:
        given = [x for x in (self.content, self.reask_of, self.clarify_choice) if x is not None]
        if len(given) != 1:
            raise ValueError("send exactly one of `content`, `reask_of` and `clarify_choice`")
        return self


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
    body: CreateSession,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> Response:
    """Create a session. `document_ids` omitted/empty = all documents; foreign or deleted ids -> 404. A retry
    with the same `Idempotency-Key` returns the same session."""
    key = check_key(idempotency_key)

    async def op() -> tuple[int, dict[str, Any]]:
        return 201, _json(await svc.create_session(db, user.id, body.document_ids, body.title, commit=False))

    h = request_hash(
        route="POST /v1/chat/sessions",
        document_ids=sorted(str(d) for d in set(body.document_ids or [])),
        title=body.title,
    )
    try:
        status, out = await run_idempotent(db, user.id, key, h, op)
    except svc.ScopeDocumentNotFound:
        raise DomainError(ErrorCode.DOCUMENT_NOT_FOUND) from None
    return canonical_response(status, out)


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


@router.get(
    "/events/schema",
    summary="JSON Schema of the SSE turn events",
    response_model=EventSchemaOut,
    response_model_exclude_unset=True,
)
async def events_schema(user: CurrentUser) -> dict[str, Any]:
    """JSON Schema of the SSE turn events (`TurnEvent`, discriminated on `type`), to generate client types."""
    return event_schema()


@router.post(
    "/sessions/{session_id}/messages",
    status_code=202,
    summary="Post a message and get a cited answer",
    dependencies=[Depends(enforce_chat_quota)],
    response_model=None,
    responses={
        202: {"model": PostMessageOut, "description": "Turn accepted (or finished): run and message ids"},
        200: {
            "description": "Retried client_message_id of a finished turn: the stored result. With "
            "`Accept: text/event-stream` the response is an SSE stream of turn events (see "
            "`GET /v1/chat/events/schema`)",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        404: {"description": "Not your session, or `reask_of` is not a message of it"},
        409: {"description": "Another turn is running in this session (`turn_in_progress`)"},
        503: {"description": "Model provider or capacity unavailable"},
    },
)
async def post_message(
    session_id: uuid.UUID,
    body: PostMessage,
    request: Request,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    response: Response,
    run_turn: Annotated[TurnRunner, Depends(get_turn_runner)],
) -> Any:
    """Start a turn. Retries of one `client_message_id` return the same message/run (200 when finished, 202
    while running). Another live turn in the session -> 409 `turn_in_progress`. A new turn runs to completion
    before the response and returns 200 with the assistant message; it keeps running and persists even if the
    client disconnects. With `Accept: text/event-stream` the turn streams events instead: the
    status codes above are decided before the stream starts and the stream itself answers 200."""
    sse = "text/event-stream" in request.headers.get("accept", "")
    content = body.content
    if body.reask_of is not None:
        try:
            await svc.get_session(db, user.id, session_id)
        except svc.SessionNotFound:
            raise DomainError(ErrorCode.NOT_FOUND) from None
        content = await history.original_question(db, user.id, session_id, body.reask_of)
        if content is None:
            raise DomainError(ErrorCode.NOT_FOUND)
    pick: uuid.UUID | None = None
    if body.clarify_choice is not None:
        try:
            await svc.get_session(db, user.id, session_id)
        except svc.SessionNotFound:
            raise DomainError(ErrorCode.NOT_FOUND) from None
        chosen = await history.clarify_pick(
            db, user.id, session_id, body.clarify_choice.message_id, body.clarify_choice.option
        )
        if chosen is None:
            raise DomainError(ErrorCode.NOT_FOUND)
        content, pick = chosen
    assert content is not None
    replaced: uuid.UUID | None = None
    if body.replaces is not None:
        replaced = await history.run_of_message(db, user.id, session_id, body.replaces)
        if replaced is None:
            raise DomainError(ErrorCode.NOT_FOUND)
    try:
        t = await svc.start_turn(db, user.id, session_id, content, body.client_message_id, body.mode)
    except svc.SessionNotFound:
        raise DomainError(ErrorCode.NOT_FOUND) from None
    except svc.TurnInProgress:
        raise DomainError(ErrorCode.TURN_IN_PROGRESS) from None
    except svc.ClientMessageIdReused:
        raise DomainError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED, "client_message_id was used with different content"
        ) from None
    if t.created and (pick is not None or replaced is not None):  # read back by `load_context`
        hints: dict[str, Any] = {}
        if pick is not None:
            hints["clarify_pick"] = {"document_id": str(pick)}
        if replaced is not None:
            hints["supersedes"] = str(replaced)
        await svc.set_run_hints(db, user.id, t.run_id, hints)
    if sse:
        if t.created:
            sink, turn = stream_mod.start_sink_task(
                lambda: run_turn(user.id, session_id, t.run_id, t.user_message_id, content, body.mode),
                _TASKS,
            )
            frames = stream_mod.stream_new_turn(db, user.id, t.run_id, t.user_message_id, sink, turn)
        else:
            frames = stream_mod.stream_existing_turn(db, user.id, t.run_id, t.user_message_id)
        return StreamingResponse(frames, media_type="text/event-stream", headers=stream_mod.SSE_HEADERS)
    if t.created:
        task: asyncio.Future[None] = asyncio.ensure_future(
            run_turn(user.id, session_id, t.run_id, t.user_message_id, content, body.mode)
        )
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        await asyncio.shield(task)  # a client disconnect cancels this await, not the turn
        done = await svc.start_turn(db, user.id, session_id, content, body.client_message_id, body.mode)
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
