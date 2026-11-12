"""API-key authentication (design §16). Keys are high-entropy random tokens; only their SHA-256 is stored."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.core.error_codes import ErrorCode
from app.core.logging import bind
from app.db.models import User

KEY_PREFIX = "vk_"


def new_api_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class AuthUser:
    id: uuid.UUID
    email: str
    is_admin: bool


def _unauthenticated() -> DomainError:
    return DomainError(ErrorCode.UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})


async def current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AuthUser:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX):
        raise _unauthenticated()
    row = (await db.execute(select(User).where(User.api_key_hash == hash_api_key(token.strip())))).scalar()
    if row is None:
        raise _unauthenticated()
    bind(user_id=str(row.id))
    return AuthUser(id=row.id, email=row.email, is_admin=row.is_admin)


CurrentUser = Annotated[AuthUser, Depends(current_user)]
