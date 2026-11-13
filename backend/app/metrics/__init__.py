"""Read-only aggregates behind the `/v1/metrics/*` endpoints (design 12). Owner scope is explicit."""

import uuid
from typing import Any

# Window names accepted by the processing and chat endpoints, in seconds.
WINDOWS_S: dict[str, int] = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}


def scope_sql(owner_id: uuid.UUID | None, column: str) -> str:
    """A constant SQL fragment: `AND <column> = :o` for owner scope, nothing for global scope."""
    return f"AND {column} = :o" if owner_id is not None else ""


def scope_params(owner_id: uuid.UUID | None, **extra: Any) -> dict[str, Any]:
    return {"o": owner_id, **extra} if owner_id is not None else dict(extra)


def num(v: Any, ndigits: int = 3) -> float | None:
    """numeric/double -> rounded float; None stays None."""
    return None if v is None else round(float(v), ndigits)
