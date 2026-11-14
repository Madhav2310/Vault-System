"""API-key authentication. Keys are high-entropy random tokens; only their SHA-256 is stored."""

import hashlib
import hmac
import secrets
import time
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
from app.core.settings import get_settings
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


SESSION_COOKIE = "vault_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "vault-ui"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _now() -> int:
    return int(time.time())


def _sign(user_id: uuid.UUID, exp: int, api_key_hash: str) -> str:
    """HMAC bound to the user's key hash (rotating the key kills every session) and the optional secret."""
    extra = get_settings().session_secret
    key = hashlib.sha256(
        b"vault-session|"
        + api_key_hash.encode()
        + b"|"
        + (extra.get_secret_value().encode() if extra else b"")
    ).digest()
    return hmac.new(key, f"{user_id}.{exp}".encode(), hashlib.sha256).hexdigest()


def issue_session_token(user_id: uuid.UUID, api_key_hash: str) -> str:
    exp = _now() + get_settings().session_ttl_s
    return f"{user_id}.{exp}.{_sign(user_id, exp, api_key_hash)}"


async def _user_from_session(db: AsyncSession, token: str) -> User | None:
    try:
        uid_s, exp_s, sig = token.split(".")
        uid, exp = uuid.UUID(uid_s), int(exp_s)
    except ValueError:
        return None
    if exp < _now():
        return None
    row = (await db.execute(select(User).where(User.id == uid))).scalar()
    if row is None or not hmac.compare_digest(sig, _sign(uid, exp, row.api_key_hash)):
        return None
    return row


async def current_user(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AuthUser:
    """`Authorization: Bearer` wins when present; otherwise the httpOnly session cookie, which needs the CSRF
    header on state-changing methods (cross-site requests cannot set it: we serve no CORS preflight)."""
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.startswith(KEY_PREFIX):
            raise _unauthenticated()
        row = (
            await db.execute(select(User).where(User.api_key_hash == hash_api_key(token.strip())))
        ).scalar()
    else:
        cookie = request.cookies.get(SESSION_COOKIE)
        if not cookie:
            raise _unauthenticated()
        row = await _user_from_session(db, cookie)
        if (
            row is not None
            and request.method not in _SAFE_METHODS
            and request.headers.get(CSRF_HEADER) != CSRF_VALUE
        ):
            raise DomainError(
                ErrorCode.FORBIDDEN,
                f"Cookie-authenticated {request.method} requires X-Requested-With: {CSRF_VALUE}",
            )
    if row is None:
        raise _unauthenticated()
    bind(user_id=str(row.id))
    return AuthUser(id=row.id, email=row.email, is_admin=row.is_admin)


CurrentUser = Annotated[AuthUser, Depends(current_user)]
