"""ASGI entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import documents, health
from app.api.errors import install_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.core.logging import configure_logging
from app.core.settings import get_settings
from app.llm.routing import ModelRouter
from app.observability.otel import instrument_fastapi, setup_telemetry


def create_app(*, telemetry: bool = False) -> FastAPI:
    """`telemetry=True` (the served app only) installs OTel on startup; tests build apps without it."""
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
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(documents.router)
    return app


app = create_app(telemetry=True)
