"""ASGI entry point."""

from fastapi import FastAPI

from app import __version__


def create_app() -> FastAPI:
    return FastAPI(title="Vault", version=__version__)


app = create_app()
