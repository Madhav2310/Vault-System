"""Helpers for brief tests: a gateway over FakeProvider with the real prompt registry, scripted brief replies
and small evidence sets. No network."""

import json
import uuid
from typing import Any

from app.chat.evidence import EvidenceChunk, EvidenceSet, build_evidence
from app.core.settings import Settings
from app.ingestion.sentences import split_sentences
from app.llm.gateway import LlmGateway, MemoryLedger
from app.llm.pricing import DEFAULT_PRICES, PriceTable
from app.llm.prompts import default_registry
from app.llm.providers import fake
from tests.support.llm import SleepRecorder

CONTRACT_TEXT = (
    "This Services Agreement is made on June 1, 2020 between Medalist Diversified REIT, Inc. and Acme Consulting LLC. "
    "The Consultant shall provide the Services for twelve months. "
    "The fee is $1,250,000 payable in four installments."
)
LAW_TEXT = "This Agreement is governed by the laws of the State of Delaware."


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+asyncpg://x/y", redis_url="redis://x", **kw)  # type: ignore[call-arg]


def brief_gateway(
    script: list[fake.Step] | None = None, **settings_kw: Any
) -> tuple[LlmGateway, fake.FakeProvider, MemoryLedger]:
    prov = fake.FakeProvider(script=script or [])
    ledger = MemoryLedger()
    gw = LlmGateway(
        chat=prov,
        embeddings=prov,
        prompts=default_registry(),
        prices=PriceTable({**DEFAULT_PRICES}),
        ledger=ledger,
        settings=settings(**settings_kw),
        sleep=SleepRecorder(),
        rng=lambda: 0.5,
    )
    return gw, prov, ledger


def chunk(
    text: str, *, title: str = "Services Agreement", page: int = 1, section: str = "Term"
) -> EvidenceChunk:
    return EvidenceChunk(
        uuid.uuid4(), uuid.uuid4(), title, 1, page, page, (section,), text, tuple(split_sentences(text))
    )


def contract_evidence() -> EvidenceSet:
    return build_evidence([chunk(CONTRACT_TEXT), chunk(LAW_TEXT, page=2, section="Law")])


def brief_reply(
    facts: list[dict[str, Any]] | None = None,
    *,
    category: str = "contract",
    summary: str = "A twelve month services agreement.",
    usage: Any = None,
) -> fake.FakeReply:
    body = {
        "document_type": "services agreement",
        "summary": summary,
        "category": category,
        "tags": ["consulting", "Real Estate"],
        "suggested_questions": ["Who are the parties?", "What is the fee?", "When does it start?"],
        "facts": facts or [],
    }
    return fake.structured(body, **({"usage": usage} if usage is not None else {}))


def merge_reply(category: str = "contract") -> fake.FakeReply:
    return fake.structured(
        {
            "document_type": "services agreement",
            "summary": "Merged summary of the whole agreement.",
            "category": category,
            "tags": ["merged", "contract"],
            "suggested_questions": ["Who are the parties?", "What is the fee?", "When does it end?"],
        }
    )


def request_inputs(req: Any) -> dict[str, Any]:
    """The JSON inside the `<inputs>` tag of a gateway `inputs=` call."""
    content: str = req.messages[-1]["content"]
    start = content.index("<inputs>") + len("<inputs>")
    parsed: dict[str, Any] = json.loads(content[start : content.rindex("</inputs>")])
    return parsed
