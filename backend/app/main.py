"""ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from app import __version__
from app.api import chat, chunks, documents, feedback, health, metrics, versions
from app.api.errors import install_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.openapi import install_openapi
from app.core.logging import configure_logging
from app.core.ratelimit import RateLimitHeadersMiddleware, enforce_rate_limit
from app.core.settings import get_settings
from app.llm.routing import ModelRouter
from app.observability.otel import instrument_fastapi, setup_telemetry

_UI_CANDIDATES = (
    Path(__file__).resolve().parents[1] / "ui_dist",  # the api image (node build stage)
    Path(__file__).resolve().parents[2] / "frontend" / "dist",  # a checkout after `make ui`
)


def _mount_ui(app: FastAPI, dist: Path | None) -> None:
    """Serve the built SPA at /app, falling back to index.html. Without a build, nothing is registered."""
    root = dist or next((c for c in _UI_CANDIDATES if (c / "index.html").is_file()), None)
    if root is None or not (root / "index.html").is_file():
        return
    root = root.resolve()
    index = root / "index.html"

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    async def spa(path: str = "") -> FileResponse:
        target = (root / path).resolve()
        if path and target.is_file() and target.is_relative_to(root):
            return FileResponse(target)
        return FileResponse(index)


def create_app(*, telemetry: bool = False, ui_dist: Path | None = None) -> FastAPI:
    """`telemetry=True` (the served app only) installs OTel on startup; tests build apps without it.
    `ui_dist` overrides where the built UI is looked up (tests)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    ModelRouter.from_settings(settings)  # fail fast: a bad MODEL_* / MODEL_ROUTES value names the node

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if telemetry:
            provider = setup_telemetry(settings, settings.otel_service_name)
            instrument_fastapi(app, tracer_provider=provider)
        yield

    app = FastAPI(title="Vault", version=__version__, lifespan=lifespan)
    install_error_handlers(app)
    install_openapi(app)
    app.add_middleware(RateLimitHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    limited = [Depends(enforce_rate_limit)]  # per-user token bucket on every authenticated router (S14.01)
    app.include_router(health.router)
    app.include_router(documents.router, dependencies=limited)
    app.include_router(versions.router, dependencies=limited)
    app.include_router(chunks.router, dependencies=limited)
    app.include_router(chat.router, dependencies=limited)
    app.include_router(metrics.router, dependencies=limited)
    app.include_router(feedback.router, dependencies=limited)
    _mount_ui(app, ui_dist)
    return app


app = create_app(telemetry=True)
