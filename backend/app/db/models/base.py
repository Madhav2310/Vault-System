"""Declarative base. The schema itself is owned by migrations; models must match it (drift test)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import REAL, BigInteger, Boolean, DateTime, Double, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.ids import uuid7

Json = dict[str, Any] | list[Any]


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        uuid.UUID: UUID(as_uuid=True),
        str: Text(),
        int: Integer(),
        bool: Boolean(),
        float: REAL(),
        Decimal: Numeric(10, 6),
        dt.datetime: DateTime(timezone=True),
        list[str]: ARRAY(Text()),
        list[uuid.UUID]: ARRAY(UUID(as_uuid=True)),
        Json: JSONB(),
    }


class CreatedAt:
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class UpdatedAt:
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid7)


__all__ = ["REAL", "Base", "BigInteger", "CreatedAt", "Double", "Json", "UpdatedAt", "pk"]
