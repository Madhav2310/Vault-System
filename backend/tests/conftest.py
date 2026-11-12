import os
import pathlib

import pytest

_TESTS = pathlib.Path(__file__).parent

# Unit tests must import app modules without a .env; these are placeholders, never contacted by unit tests.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://vault:vault@localhost:5432/vault")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory: tests/unit → unit, tests/integration → integration, tests/e2e → e2e."""
    for item in items:
        rel = pathlib.Path(str(item.fspath)).relative_to(_TESTS)
        if rel.parts and rel.parts[0] in {"unit", "integration", "e2e"}:
            item.add_marker(getattr(pytest.mark, rel.parts[0]))
