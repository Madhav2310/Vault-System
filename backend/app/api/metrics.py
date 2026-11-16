"""Metrics endpoints. Owner-scoped; `?scope=global` is admin only."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.api.schemas import ChatMetrics, DocumentsMetrics, ProcessingMetrics
from app.core.auth import AuthUser, CurrentUser
from app.core.error_codes import ErrorCode
from app.metrics.chat import chat_metrics
from app.metrics.documents import document_metrics
from app.metrics.processing import processing_metrics

router = APIRouter(prefix="/v1/metrics", tags=["Metrics"])

Scope = Annotated[Literal["owner", "global"], Query(description="`global` is admin only")]
Window = Annotated[Literal["1h", "24h", "7d"], Query(description="Look-back window")]
Db = Annotated[AsyncSession, Depends(get_db)]


def _owner(user: AuthUser, scope: str) -> uuid.UUID | None:
    """The owner to filter by, or None for global scope (403 unless admin)."""
    if scope == "global":
        if not user.is_admin:
            raise DomainError(ErrorCode.FORBIDDEN)
        return None
    return user.id


@router.get(
    "/documents",
    summary="Document metrics",
    description="Counts by status, type and category, pages, chunks, storage and time to ready.",
    response_model=DocumentsMetrics,
    responses={403: {"description": "`scope=global` is admin only"}},
)
async def documents_metrics(user: CurrentUser, db: Db, scope: Scope = "owner") -> dict[str, Any]:
    return await document_metrics(db, _owner(user, scope))


@router.get(
    "/processing",
    summary="Processing metrics",
    description="Throughput, stage durations, failures by error code, retries and queue age.",
    response_model=ProcessingMetrics,
    responses={403: {"description": "`scope=global` is admin only"}},
)
async def processing_metrics_endpoint(
    user: CurrentUser, db: Db, scope: Scope = "owner", window: Window = "24h"
) -> dict[str, Any]:
    return await processing_metrics(db, _owner(user, scope), window)


@router.get(
    "/chat",
    summary="Chat metrics",
    description="Turns, outcomes, coverage, latency and TTFT percentiles, cost, tokens and answer feedback.",
    response_model=ChatMetrics,
    responses={403: {"description": "`scope=global` is admin only"}},
)
async def chat_metrics_endpoint(
    user: CurrentUser, db: Db, scope: Scope = "owner", window: Window = "24h"
) -> dict[str, Any]:
    return await chat_metrics(db, _owner(user, scope), window)
