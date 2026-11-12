import pathlib

import pytest

_TESTS = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory: tests/unit → unit, tests/integration → integration, tests/e2e → e2e."""
    for item in items:
        rel = pathlib.Path(str(item.fspath)).relative_to(_TESTS)
        if rel.parts and rel.parts[0] in {"unit", "integration", "e2e"}:
            item.add_marker(getattr(pytest.mark, rel.parts[0]))
