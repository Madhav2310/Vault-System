"""Repository base classes.

Every query on an owned table (one with `owner_id`) goes through `OwnedRepository`, whose public methods
all take `owner_id` explicitly and whose `_scoped()` helper always adds the owner predicate. A contract
test (`tests/unit/db/test_repository_contract.py`) enforces this for every repository in `app/`.
"""

import uuid
from collections.abc import Sequence
from typing import Any, ClassVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Base


class MissingOwnerError(ValueError):
    pass


def is_owned(model: type[Base]) -> bool:
    return "owner_id" in model.__table__.columns


class Repository:
    """For tables without an owner (blobs, eval_runs, llm_cache, …). Must not be used for owned models."""

    model: ClassVar[type[Base]]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session


class OwnedRepository(Repository):
    """Base for owned tables.

    Subclass methods must take `owner_id: uuid.UUID` and build queries via `_scoped`.
    """

    def _scoped(self, owner_id: uuid.UUID, *entities: Any) -> Select[Any]:
        if not isinstance(owner_id, uuid.UUID):
            raise MissingOwnerError("owner_id is required for owned tables")
        stmt = select(*entities) if entities else select(self.model)
        return stmt.where(self.model.__table__.c.owner_id == owner_id)

    async def get(self, owner_id: uuid.UUID, id: uuid.UUID) -> Any | None:
        stmt = self._scoped(owner_id).where(self.model.__table__.c.id == id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(self, owner_id: uuid.UUID, limit: int = 100) -> Sequence[Any]:
        result = await self.session.execute(self._scoped(owner_id).limit(limit))
        return list(result.scalars().all())

    async def add(self, owner_id: uuid.UUID, obj: Any) -> Any:
        if getattr(obj, "owner_id", None) not in (None, owner_id):
            raise MissingOwnerError("object owner_id does not match the caller's owner_id")
        obj.owner_id = owner_id
        self.session.add(obj)
        await self.session.flush()
        return obj
