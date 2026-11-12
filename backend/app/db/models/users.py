import uuid

from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, pk


class User(CreatedAt, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str | None]
    api_key_hash: Mapped[str] = mapped_column(unique=True)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
