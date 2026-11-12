import datetime as dt
import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json, UpdatedAt, pk


class ChatSession(CreatedAt, UpdatedAt, Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None]
    scope_document_ids: Mapped[list[uuid.UUID] | None]
    rolling_summary: Mapped[str | None]
    resolved_entities: Mapped[Json] = mapped_column(default=dict, server_default="{}")
    summary_upto_message_id: Mapped[uuid.UUID | None]
    active_run_id: Mapped[uuid.UUID | None]
    run_lease_expires_at: Mapped[dt.datetime | None]


class ChatMessage(CreatedAt, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "client_message_id", name="uq_chat_messages_session_client_id"),
    )
    id: Mapped[uuid.UUID] = pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id"))
    role: Mapped[str]
    client_message_id: Mapped[str | None]
    content: Mapped[str]
    response_type: Mapped[str | None]
    coverage_state: Mapped[str | None]
    run_id: Mapped[uuid.UUID | None]
    followups: Mapped[Json | None]
    stale_since: Mapped[dt.datetime | None]
