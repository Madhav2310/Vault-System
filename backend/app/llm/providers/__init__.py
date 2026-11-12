"""Provider adapters. Only modules in this package construct provider SDK clients
(`tests/unit/test_architecture.py::test_no_provider_clients_outside_llm`)."""

from collections.abc import AsyncGenerator, Sequence
from typing import Protocol

from app.llm.types import ChatRequest, ChatResponse, EmbeddingResponse, StreamEvent


class ChatProvider(Protocol):
    name: str

    async def complete(self, req: ChatRequest) -> ChatResponse: ...

    def stream(self, req: ChatRequest) -> AsyncGenerator[StreamEvent, None]:
        """Yields TextDelta / CitationDelta, then one ProviderFinal. Raises ProviderError subclasses."""
        ...


class EmbeddingProvider(Protocol):
    name: str

    async def embed(self, model: str, texts: Sequence[str], dimensions: int) -> EmbeddingResponse: ...
