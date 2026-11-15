"""Custom-summary task (`ai` queue). The payload is the `document_insights` row id only; everything else is
read from the DB. Idempotent: see `app.insights.summaries.run_summary` (CAS claim with a lease, `done` rows
are never overwritten)."""

import uuid

from app.core.settings import get_settings
from app.insights.summaries import SUMMARY_TASK, retriever_select, run_summary
from app.llm.gateway import build_gateway
from app.retrieval.query_embed import QueryEmbedder, cache_client
from app.retrieval.rerank import RerankClient
from app.retrieval.retrieve import Retriever
from app.worker.celery_app import celery_app
from app.worker.runtime import SessionFactory, run_db


@celery_app.task(name=SUMMARY_TASK)
def summary_task(summary_id: str) -> str:
    async def go(sm: SessionFactory) -> str:
        settings = get_settings()
        gateway = build_gateway(sm, settings)
        rds = cache_client(settings.redis_url)
        reranker = RerankClient.from_settings(settings)
        try:
            retriever = Retriever(QueryEmbedder(gateway, rds), reranker, rds)
            return await run_summary(sm, gateway, retriever_select(retriever, sm), uuid.UUID(summary_id))
        finally:
            await reranker.aclose()
            await rds.aclose()

    return run_db(go)
