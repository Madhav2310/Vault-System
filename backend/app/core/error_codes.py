"""Central error catalog. Every API error carries one of these codes."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class ErrorSpec:
    status: int
    title: str
    retryable: bool


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    DOCUMENT_NOT_FOUND = "document_not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    TURN_IN_PROGRESS = "turn_in_progress"
    CONFLICT = "conflict"
    FILE_TOO_LARGE = "file_too_large"
    UNSUPPORTED_TYPE = "unsupported_type"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    INTERNAL_ERROR = "internal_error"
    CAPACITY = "capacity"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"

    @property
    def spec(self) -> ErrorSpec:
        return ERROR_SPECS[self]


ERROR_SPECS: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.VALIDATION_ERROR: ErrorSpec(422, "Request validation failed", False),
    ErrorCode.UNAUTHENTICATED: ErrorSpec(401, "Authentication required", False),
    ErrorCode.FORBIDDEN: ErrorSpec(403, "Forbidden", False),
    ErrorCode.NOT_FOUND: ErrorSpec(404, "Not found", False),
    ErrorCode.DOCUMENT_NOT_FOUND: ErrorSpec(404, "Document not found", False),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorSpec(405, "Method not allowed", False),
    ErrorCode.TURN_IN_PROGRESS: ErrorSpec(409, "A turn is already running in this session", True),
    ErrorCode.CONFLICT: ErrorSpec(409, "Conflict", False),
    ErrorCode.FILE_TOO_LARGE: ErrorSpec(413, "File too large", False),
    ErrorCode.UNSUPPORTED_TYPE: ErrorSpec(415, "Unsupported file type", False),
    ErrorCode.IDEMPOTENCY_KEY_REUSED: ErrorSpec(
        422, "Idempotency key reused with a different request", False
    ),
    ErrorCode.QUOTA_EXCEEDED: ErrorSpec(429, "Quota exceeded", True),
    ErrorCode.RATE_LIMITED: ErrorSpec(429, "Rate limited", True),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(500, "Internal error", True),
    ErrorCode.CAPACITY: ErrorSpec(503, "Service at capacity", True),
    ErrorCode.DEPENDENCY_UNAVAILABLE: ErrorSpec(503, "A dependency is unavailable", True),
}
