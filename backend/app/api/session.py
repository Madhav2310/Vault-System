"""Browser session: exchange an API key for an httpOnly, SameSite=Strict signed cookie (S22.11, design §16).

The UI never holds the key after login; curl/Postman keep using `Authorization: Bearer`. Tokens are stateless
HMACs bound to the user's key hash (see `app.core.auth`): rotating the key invalidates them, and logout clears
the cookie client-side."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.errors import DomainError
from app.core.auth import (
    CSRF_HEADER,
    CSRF_VALUE,
    KEY_PREFIX,
    SESSION_COOKIE,
    CurrentUser,
    hash_api_key,
    issue_session_token,
    new_api_key,
)
from app.core.error_codes import ErrorCode
from app.core.settings import get_settings
from app.db.models import User

router = APIRouter(prefix="/v1", tags=["Session"])
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class SessionIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=256, description="Your API key (`vk_...`)")


def _secure(request: Request) -> bool:
    return request.url.hostname not in _LOCAL_HOSTS


@router.post(
    "/session",
    summary="Log in with an API key",
    description="Sets `vault_session` (httpOnly, SameSite=Strict; `Secure` unless the host is localhost).",
    operation_id="login_session",
    status_code=204,
    responses={401: {"description": "Invalid API key"}},
)
async def login(body: SessionIn, request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    key = body.api_key.strip()
    row = None
    if key.startswith(KEY_PREFIX):
        row = (await db.execute(select(User).where(User.api_key_hash == hash_api_key(key)))).scalar()
    if row is None:
        raise DomainError(ErrorCode.UNAUTHENTICATED, headers={"WWW-Authenticate": "Bearer"})
    resp = Response(status_code=204)
    resp.set_cookie(
        SESSION_COOKIE,
        issue_session_token(row.id, row.api_key_hash),
        max_age=get_settings().session_ttl_s,
        path="/",
        httponly=True,
        samesite="strict",
        secure=_secure(request),
    )
    return resp


LOCAL_USER_EMAIL = "local@vault.local"


@router.post(
    "/session/local",
    summary="Sign in as the local user (local runs only)",
    description=(
        "For a laptop run: when `LOCAL_AUTOLOGIN` is on, the environment is not `prod` and the request host "
        "is localhost, signs in as one fixed local user (created on first use; its key is never shown). "
        "404 everywhere else, so a deployed instance still needs a real API key."
    ),
    operation_id="login_local_session",
    status_code=204,
    responses={404: {"description": "Not a local run, or auto sign-in is switched off"}},
)
async def login_local(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    st = get_settings()
    if not st.local_autologin or st.environment == "prod" or request.url.hostname not in _LOCAL_HOSTS:
        raise DomainError(ErrorCode.NOT_FOUND)
    row = (await db.execute(select(User).where(User.email == LOCAL_USER_EMAIL))).scalar()
    if row is None:
        row = User(email=LOCAL_USER_EMAIL, api_key_hash=hash_api_key(new_api_key()), is_admin=False)
        db.add(row)
        await db.commit()
    resp = Response(status_code=204)
    resp.set_cookie(
        SESSION_COOKIE,
        issue_session_token(row.id, row.api_key_hash),
        max_age=st.session_ttl_s,
        path="/",
        httponly=True,
        samesite="strict",
        secure=False,
    )
    return resp


@router.delete(
    "/session",
    summary="Log out",
    description=f"Clears the session cookie. Cookie-authenticated calls need `{CSRF_HEADER}: {CSRF_VALUE}`.",
    operation_id="logout_session",
    status_code=204,
)
async def logout(_user: CurrentUser, request: Request) -> Response:
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict", secure=_secure(request))
    return resp


@router.get(
    "/session",
    summary="Check the current credentials",
    description="204 when the cookie or Bearer key is valid, 401 otherwise. No side effects.",
    operation_id="get_session",
    status_code=204,
)
async def check(_user: CurrentUser) -> Response:
    return Response(status_code=204)
