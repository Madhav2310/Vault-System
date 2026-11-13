"""Answer feedback endpoint (design §9.6, §12)."""

import uuid
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.api.schemas import FeedbackOut
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.feedback import service

router = APIRouter(prefix="/v1", tags=["Chat"])


class FeedbackIn(BaseModel):
    message_id: uuid.UUID
    rating: Literal["up", "down"]
    reason: Literal["wrong", "unsupported", "incomplete", "other"] | None = None
    segment_ordinal: int | None = Field(
        default=None, ge=0, description="Rate one segment instead of the answer"
    )
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reason_only_on_down(self) -> Self:
        if self.reason is not None and self.rating != "down":
            raise ValueError("reason is only allowed with rating 'down'")
        return self


@router.post(
    "/feedback",
    summary="Rate an answer",
    response_model=FeedbackOut,
    responses={404: {"description": "Not your message, or not an answer"}},
)
async def post_feedback(
    body: FeedbackIn, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    """Rate an assistant answer (or one of its segments). Idempotent per (message, segment): posting again
    replaces your earlier rating. Another user's message, or one that is not an answer, is 404."""
    try:
        row = await service.record_feedback(
            db, user.id, body.message_id, body.rating, body.reason, body.segment_ordinal, body.note
        )
    except service.MessageNotFound:
        raise DomainError(ErrorCode.NOT_FOUND) from None
    except service.SegmentOutOfRange:
        raise DomainError(ErrorCode.VALIDATION_ERROR, "segment_ordinal is out of range") from None
    return {
        "id": str(row.id),
        "message_id": str(row.message_id),
        "rating": row.rating,
        "reason": row.reason,
        "segment_ordinal": row.segment_ordinal,
        "note": row.note,
    }
