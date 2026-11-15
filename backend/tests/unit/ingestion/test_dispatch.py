import uuid

from app.ingestion import jobs


async def test_payload_only_job_id(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.worker.celery_app import celery_app

    sent: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(celery_app, "send_task", lambda *a, **kw: sent.append((a, kw)))
    job_id = uuid.uuid4()
    await jobs.celery_enqueue()("embed", job_id)
    assert sent == [(("vault.ai.stage",), {"args": [str(job_id)]})]
    assert set(jobs.STAGE_TASKS) == {
        "extract",
        "quality",
        "chunk",
        "embed",
        "index",
        "brief",
    }
