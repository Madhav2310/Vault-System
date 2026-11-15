"""All ORM models; importing this module registers every table on `Base.metadata`."""

from app.db.models.base import Base
from app.db.models.chat import ChatMessage, ChatSession
from app.db.models.chunks import Chunk
from app.db.models.documents import Blob, Document, DocumentVersion, IdempotencyKey, Page
from app.db.models.eval import EvalResult, EvalRun
from app.db.models.feedback import Feedback
from app.db.models.insights import DocumentInsight, LlmCache
from app.db.models.jobs import JobEvent, ProcessingJob
from app.db.models.ledger import (
    AnswerSegment,
    LlmCall,
    RagRun,
    RetrievalCandidate,
    RunStep,
    RunVersion,
    SegmentEvidence,
)
from app.db.models.users import User

__all__ = [
    "AnswerSegment", "Base", "Blob", "ChatMessage", "ChatSession", "Chunk", "Document", "DocumentInsight",
    "DocumentVersion", "EvalResult", "EvalRun", "Feedback", "IdempotencyKey", "JobEvent", "LlmCache",
    "LlmCall", "Page", "ProcessingJob", "RagRun",
    "RetrievalCandidate", "RunStep", "RunVersion", "SegmentEvidence", "User",
]  # fmt: skip
