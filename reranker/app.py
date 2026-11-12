"""Rerank service. S0 stub: returns identity scores (input order preserved); S2 swaps in fastembed."""

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="vault-reranker", version="0.1.0")


class RerankRequest(BaseModel):
    query: str
    documents: list[str] = Field(max_length=200)


class RerankResponse(BaseModel):
    scores: list[float]
    model: str


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/rerank")
def rerank(req: RerankRequest) -> RerankResponse:
    n = len(req.documents)
    return RerankResponse(scores=[1.0 - i / max(n, 1) for i in range(n)], model="identity-stub")
