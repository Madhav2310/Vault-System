"""Helpers for generate-node tests: a gateway over FakeProvider with the real prompt registry (so `answer_rag`
resolves), and small evidence sets."""

import uuid

from app.chat.evidence import EvidenceChunk, EvidenceSet, build_evidence
from app.core.settings import Settings
from app.ingestion.sentences import split_sentences
from app.llm.gateway import LedgerWriter, LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers.fake import FakeProvider, Step
from tests.support.llm import SleepRecorder

TEXTS = (
    "The Consultant shall provide the Services for twelve months. "
    "Either party may terminate on 30 days notice.",
    "This Agreement is governed by the laws of the State of Delaware.",
)


def chunk(text: str, title: str = "Services Agreement", page: int = 3) -> EvidenceChunk:
    sents = tuple(split_sentences(text))
    return EvidenceChunk(uuid.uuid4(), uuid.uuid4(), title, 1, page, page, ("Term",), text, sents)


def evidence() -> EvidenceSet:
    return build_evidence([chunk(t) for t in TEXTS])


def fake_gateway(
    script: list[Step] | None = None, ledger: LedgerWriter | None = None
) -> tuple[LlmGateway, FakeProvider]:
    fake = FakeProvider(script=script or [])
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x")  # type: ignore[call-arg]
    gw = LlmGateway(
        chat=fake,
        embeddings=fake,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger or MemoryLedger(),
        settings=settings,
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )
    return gw, fake
