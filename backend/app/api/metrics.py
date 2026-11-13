"""Metrics endpoints (design 12). Owner-scoped; `?scope=global` is admin only."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.core.auth import AuthUser, CurrentUser
from app.core.error_codes import ErrorCode
from app.metrics.documents import document_metrics

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])

Scope = Annotated[Literal["owner", "global"], Query(description="`global` is admin only")]
Db = Annotated[AsyncSession, Depends(get_db)]


def _owner(user: AuthUser, scope: str) -> uuid.UUID | None:
    """The owner to filter by, or None for global scope (403 unless admin)."""
    if scope == "global":
        if not user.is_admin:
            raise DomainError(ErrorCode.FORBIDDEN)
        return None
    return user.id


@router.get("/documents")
async def documents_metrics(user: CurrentUser, db: Db, scope: Scope = "owner") -> dict[str, Any]:
    return await document_metrics(db, _owner(user, scope))
