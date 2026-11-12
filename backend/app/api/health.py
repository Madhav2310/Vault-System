"""Health endpoints (design §12). `/readyz` dependency checks arrive in S0.16."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Process liveness only; never touches dependencies."""
    return {"status": "ok"}
