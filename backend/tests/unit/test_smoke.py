from fastapi import FastAPI


def test_import_app() -> None:
    from app.main import app

    assert isinstance(app, FastAPI)
