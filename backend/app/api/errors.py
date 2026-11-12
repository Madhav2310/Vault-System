"""application/problem+json error responses (RFC 9457 shape + `code`, `retryable`)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.error_codes import ErrorCode
from app.core.logging import get_logger

PROBLEM_JSON = "application/problem+json"
_log = get_logger("vault.errors")

_STATUS_TO_CODE = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.FILE_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.DEPENDENCY_UNAVAILABLE,
}


class DomainError(Exception):
    """Raise from domain code; rendered as problem+json with the code's status and retryability."""

    def __init__(self, code: ErrorCode, detail: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(detail or code.spec.title)
        self.code = code
        self.detail = detail
        self.headers = headers


def problem(code: ErrorCode, detail: str | None = None, **extra: Any) -> JSONResponse:
    spec = code.spec
    body = {
        "type": f"https://vault.local/problems/{code.value}",
        "title": spec.title,
        "status": spec.status,
        "code": code.value,
        "detail": detail,
        "retryable": spec.retryable,
        **extra,
    }
    return JSONResponse(body, status_code=spec.status, media_type=PROBLEM_JSON)


async def _domain(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    resp = problem(exc.code, exc.detail)
    if exc.headers:
        resp.headers.update(exc.headers)
    return resp


async def _http(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != code.spec.title else None
    resp = problem(code, detail)
    resp.status_code = exc.status_code
    if exc.headers:
        resp.headers.update(exc.headers)
    return resp


async def _validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Field locations and messages only; never echo submitted values (may contain document text).
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
    ]
    return problem(ErrorCode.VALIDATION_ERROR, "Request validation failed", errors=errors)


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_error", error_type=type(exc).__name__)
    return problem(ErrorCode.INTERNAL_ERROR)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(Exception, _unhandled)
