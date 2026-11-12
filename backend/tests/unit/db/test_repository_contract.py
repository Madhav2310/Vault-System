import importlib
import inspect
import pkgutil
import uuid

import pytest
from sqlalchemy.dialects import postgresql

import app
from app.db.models import Document, User
from app.db.repository import MissingOwnerError, OwnedRepository, Repository, is_owned


def _all_repositories() -> list[type[Repository]]:
    for mod in pkgutil.walk_packages(app.__path__, "app."):
        if mod.name.startswith("app.db.migrations") or mod.name.endswith("__main__"):
            continue
        importlib.import_module(mod.name)
    found: list[type[Repository]] = []
    stack: list[type[Repository]] = [Repository]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            found.append(sub)
            stack.append(sub)
    return found


class _ExampleDocs(OwnedRepository):  # registered so the contract test always has a subject
    model = Document

    async def titles(self, owner_id: uuid.UUID) -> list[str]:
        rows = await self.session.execute(self._scoped(owner_id, Document.title))
        return list(rows.scalars())


def test_owner_id_required() -> None:
    repos = [r for r in _all_repositories() if getattr(r, "model", None) is not None]
    assert _ExampleDocs in repos
    for repo in repos:
        if is_owned(repo.model):
            assert issubclass(repo, OwnedRepository), f"{repo.__name__} must extend OwnedRepository"
            for name, fn in inspect.getmembers(repo, inspect.isfunction):
                if name.startswith("_"):
                    continue
                params = inspect.signature(fn).parameters
                assert "owner_id" in params, f"{repo.__name__}.{name} lacks owner_id"
                assert list(params).index("owner_id") == 1, (
                    f"{repo.__name__}.{name}: owner_id must come first"
                )
        else:
            assert not issubclass(repo, OwnedRepository), f"{repo.__name__}: unowned model in OwnedRepository"


def test_scoped_select_has_owner_predicate() -> None:
    owner = uuid.uuid4()
    repo = _ExampleDocs(session=None)  # type: ignore[arg-type]
    for stmt in (repo._scoped(owner), repo._scoped(owner, Document.title), repo._scoped(owner).limit(3)):
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert f"documents.owner_id = '{owner}'" in sql
    with pytest.raises(MissingOwnerError):
        repo._scoped(None)  # type: ignore[arg-type]
    assert is_owned(Document) and not is_owned(User)
