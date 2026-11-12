"""ASGI entry point."""

from fastapi import FastAPI

from app import __version__
from app.api import health
from app.api.errors import install_error_handlers
from app.api.middleware import RequestContextMiddleware
from app.core.logging import configure_logging
from app.core.settings import get_settings


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(title="Vault", version=__version__)
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    return app


app = create_app()
