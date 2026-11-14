"""Stage error classes.

Stages raise these; any other exception counts as a retryable `internal` error."""

from typing import ClassVar


class StageError(Exception):
    permanent: ClassVar[bool] = False
    version_status: ClassVar[str] = "failed"  # status the version takes when this ends the job

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class RetryableStageError(StageError):
    """Transient (timeouts, provider 429/5xx, storage hiccups): retried with backoff up to max_attempts."""


class PermanentStageError(StageError):
    """Retrying cannot help (corrupt file): the job ends now and the version is `failed`."""

    permanent = True


class UnsupportedContentError(PermanentStageError):
    """The file is valid but we cannot process it (encrypted PDF): the version is `unsupported`."""

    version_status = "unsupported"
