import datetime as dt
import uuid

from sqlalchemy import Double, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, CreatedAt, Json, pk


class EvalRun(CreatedAt, Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = pk()
    git_sha: Mapped[str]
    config: Mapped[Json] = mapped_column(default=dict, server_default="{}")
    started_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[dt.datetime | None]


class EvalResult(CreatedAt, Base):
    __tablename__ = "eval_results"
    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id"), primary_key=True)
    question_id: Mapped[str] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[float] = mapped_column(Double)
