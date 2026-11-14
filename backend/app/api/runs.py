"""Run endpoints: "why this answer" for the user, and the reconnect state of a running turn."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.api.schemas import RunViewOut
from app.core.auth import CurrentUser
from app.core.error_codes import ErrorCode
from app.explain.views import RunNotFound, run_view

router = APIRouter(prefix="/v1/runs", tags=["Runs"])


@router.get(
    "/{run_id}",
    summary="Why this answer",
    response_model=RunViewOut,
    response_model_exclude_unset=True,
    responses={404: {"description": "Not your run"}},
)
async def get_run(
    run_id: uuid.UUID, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    """Why this answer: status, route, reason codes, a template-rendered explanation, the sources used, the
    node steps, usage and (when finished) the stored message. `status: running` means the turn is still
    working (a client that lost its stream polls this). Another owner's run is 404."""
    try:
        return await run_view(db, user.id, run_id)
    except RunNotFound:
        raise DomainError(ErrorCode.NOT_FOUND) from None
