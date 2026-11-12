"""Provider-neutral request/response types shared by the gateway and the provider adapters."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app.llm.errors import RequestConflictError

Effort = Literal["none", "low", "medium", "high", "xhigh", "max"]
Lane = Literal["interactive", "background"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # uncached input only; adapters subtract cache reads/writes
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class SearchResult:
    """One evidence chunk → one `search_result` block; one text block per sentence (design §8.5)."""

    source: str
    title: str
    sentences: tuple[str, ...]


@dataclass(frozen=True)
class Citation:
    """A `search_result_location`: half-open block range [start_block_index, end_block_index)."""

    search_result_index: int
    start_block_index: int
    end_block_index: int
    cited_text: str
    source: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class TextPart:
    text: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class ChatRequest:
    model: str
    system: str
    messages: list[dict[str, Any]]
    max_tokens: int
    effort: Effort
    output_schema: dict[str, Any] | None = None  # → output_config.format
    search_results: tuple[SearchResult, ...] = ()  # → search_result blocks (Anthropic adapter only)
    service_tier: str | None = None  # OpenAI: e.g. "flex"; None = standard

    def __post_init__(self) -> None:
        if self.output_schema is not None and self.search_results:
            raise RequestConflictError("citations and output_config.format cannot be combined (400)")


@dataclass(frozen=True)
class ChatResponse:
    parts: tuple[TextPart, ...]
    stop_reason: str
    usage: Usage
    refusal_category: str | None = None
    resolved_model: str | None = None  # the model the API reports having served

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts)


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    usage: Usage


# --- stream events (gateway.stream yields these) ---------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    block_index: int
    text: str


@dataclass(frozen=True)
class CitationDelta:
    block_index: int
    citation: Citation


@dataclass(frozen=True)
class ProviderFinal:
    """Last event of a provider stream."""

    stop_reason: str
    usage: Usage
    refusal_category: str | None = None
    resolved_model: str | None = None


@dataclass(frozen=True)
class Final:
    """Last event of a gateway stream (only on a usable stop_reason)."""

    usage: Usage
    stop_reason: str
    cost_usd: Decimal
    call_ids: tuple[uuid.UUID, ...] = ()


StreamEvent = TextDelta | CitationDelta | ProviderFinal
