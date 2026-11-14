"""OpenAI embeddings adapter (text-embedding-3-small, 1536 dims).
SDK retries are off (the gateway retries). No OpenAI tracing/content capture is enabled here."""

from collections.abc import Sequence

import openai

from app.llm import errors
from app.llm.types import EmbeddingResponse, Usage


def map_error(e: openai.APIError) -> errors.ProviderError:
    if isinstance(e, openai.APITimeoutError):
        return errors.ProviderTimeoutError()
    if isinstance(e, openai.APIConnectionError):
        return errors.ProviderConnectionError()
    if isinstance(e, openai.APIStatusError):
        code, ra = e.status_code, errors.parse_retry_after(e.response.headers)
        if isinstance(e, openai.RateLimitError):
            return errors.RateLimitedError(status_code=code, retry_after=ra)
        if code >= 500 or code in (408, 409):
            return errors.ProviderServerError(status_code=code, retry_after=ra)
        if code in (401, 403):
            return errors.ProviderAuthError(status_code=code)
        return errors.BadRequestError(str(code), status_code=code)
    return errors.ProviderError(type(e).__name__)


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(self, api_key: str | None, timeout_s: float, client: openai.AsyncOpenAI | None = None):
        self._api_key, self._timeout_s, self._client = api_key, timeout_s, client

    @property
    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:  # lazy, like the Anthropic provider
            self._client = openai.AsyncOpenAI(api_key=self._api_key, max_retries=0, timeout=self._timeout_s)
        return self._client

    async def embed(self, model: str, texts: Sequence[str], dimensions: int) -> EmbeddingResponse:
        try:
            r = await self.client.embeddings.create(model=model, input=list(texts), dimensions=dimensions)
        except openai.APIError as e:
            raise map_error(e) from e
        vectors = [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
        return EmbeddingResponse(vectors=vectors, usage=Usage(input_tokens=r.usage.prompt_tokens))
